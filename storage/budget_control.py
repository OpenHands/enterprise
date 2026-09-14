"""Serialize budget owners across processes and persist intent before effects."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql.elements import TextClause

from storage.billing_session import BillingSession
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings


class BudgetControlConflict(RuntimeError):
    """A competing operation or a stale request cannot be applied safely."""


class BudgetWriteDenied(RuntimeError):
    """There is no durable authority to make this budget mutation."""


def budget_engine(session: AsyncSession) -> AsyncEngine:
    bound = session.bind
    if isinstance(bound, AsyncConnection):
        bound = bound.engine
    if not isinstance(bound, AsyncEngine):
        raise BudgetWriteDenied('Organization budget database is not bound')
    return bound


def budget_request_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(',', ':'), allow_nan=False
        ).encode()
    ).hexdigest()


def _lock_key(org_id: UUID) -> int:
    digest = hashlib.sha256(f'openhands-budget:{org_id}'.encode()).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


@dataclass
class BudgetControlSession:
    org_id: UUID
    session: AsyncSession
    task: asyncio.Task[Any] | None
    active: bool = True
    _uncommitted_budget_state: bool = False

    def __post_init__(self) -> None:
        event.listen(self.session.sync_session, 'after_flush', self._after_flush)
        event.listen(self.session.sync_session, 'after_commit', self._after_commit)
        event.listen(self.session.sync_session, 'after_rollback', self._after_commit)
        event.listen(self.session.sync_session, 'do_orm_execute', self._track_statement)

    def _track_statement(self, state: ORMExecuteState) -> None:
        if (
            state.is_insert
            or state.is_update
            or state.is_delete
            or isinstance(state.statement, TextClause)
        ):
            self._uncommitted_budget_state = True

    def _after_flush(self, session: Session, flush_context: Any) -> None:
        if any(
            isinstance(item, OrgBudgetSettings | OrgBudgetOperation | BillingSession)
            for item in session.new | session.dirty | session.deleted
        ):
            self._uncommitted_budget_state = True

    def _after_commit(self, session: Session) -> None:
        if not session.in_nested_transaction():
            self._uncommitted_budget_state = False

    def assert_locked(self) -> None:
        if not self.active or self.task is not asyncio.current_task():
            raise BudgetWriteDenied('Budget lock is not owned by this task')

    @property
    def engine(self) -> AsyncEngine:
        return budget_engine(self.session)

    async def settings(self) -> OrgBudgetSettings:
        self.assert_locked()
        settings = await self.session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == self.org_id)
        )
        if settings is None:
            raise BudgetWriteDenied(
                'Organization budget ownership has not been initialized'
            )
        return settings

    async def find_operation(
        self, idempotency_key: str, request_hash: str
    ) -> OrgBudgetOperation | None:
        self.assert_locked()
        operation = await self.session.scalar(
            select(OrgBudgetOperation).where(
                OrgBudgetOperation.org_id == self.org_id,
                OrgBudgetOperation.idempotency_key == idempotency_key,
            )
        )
        if operation is not None and operation.request_hash != request_hash:
            raise BudgetControlConflict(
                'Idempotency key already used for a different request'
            )
        return operation

    async def pending_operation(self) -> OrgBudgetOperation | None:
        self.assert_locked()
        return await self.session.scalar(
            select(OrgBudgetOperation).where(
                OrgBudgetOperation.org_id == self.org_id,
                OrgBudgetOperation.status == 'pending',
            )
        )

    async def pending_credit(self) -> BillingSession | None:
        self.assert_locked()
        return await self.session.scalar(
            select(BillingSession).where(
                BillingSession.org_id == self.org_id,
                BillingSession.status == 'in_progress',
                BillingSession.credit_target.is_not(None),
            )
        )

    async def authorize_credit_target(self, session_id: str, target: float) -> None:
        self.assert_locked()
        if self._uncommitted_budget_state:
            raise BudgetWriteDenied('Credit delivery intent is not durable')
        with self.session.no_autoflush:
            record = (
                await self.session.execute(
                    select(
                        BillingSession.org_id,
                        BillingSession.status,
                        BillingSession.credit_target,
                    ).where(BillingSession.id == session_id)
                )
            ).one_or_none()
        if (
            record is None
            or record.org_id != self.org_id
            or record.status != 'in_progress'
            or record.credit_target != target
        ):
            raise BudgetWriteDenied('Credit delivery has no matching durable target')
        if await self.pending_operation() is not None:
            raise BudgetControlConflict(
                'Finish the pending budget operation before delivering credit'
            )

    async def reserve_operation(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        kind: str,
        actor: str,
        plan: dict[str, Any],
    ) -> OrgBudgetOperation:
        existing = await self.find_operation(idempotency_key, request_hash)
        if existing is not None:
            return existing
        if await self.pending_credit() is not None:
            raise BudgetControlConflict(
                'Finish pending credit delivery before changing budgets'
            )
        if not idempotency_key or len(idempotency_key) > 128 or not actor:
            raise ValueError('A bounded idempotency key and actor are required')
        if kind not in {'adopt', 'settings', 'rollover', 'repair'}:
            raise ValueError('Unsupported budget operation kind')
        settings = await self.settings()
        if kind == 'adopt':
            if settings.control_mode not in {'external', 'needs_adoption'}:
                raise BudgetControlConflict('Organization is already managed')
        elif settings.control_mode != 'managed':
            raise BudgetWriteDenied('Organization budgets are not managed by OpenHands')
        if await self.pending_operation() is not None:
            raise BudgetControlConflict(
                'Finish or hand off the pending budget operation first'
            )
        settings.control_generation += 1
        operation = OrgBudgetOperation(
            org_id=self.org_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            generation=settings.control_generation,
            kind=kind,
            actor=actor,
            plan=json.loads(json.dumps(plan, allow_nan=False)),
            status='pending',
        )
        self.session.add(operation)
        # The session owns a dedicated connection, so commit does not release the lock.
        await self.session.commit()
        return operation

    async def require_executable(self, operation: OrgBudgetOperation) -> None:
        if await self.pending_credit() is not None:
            raise BudgetControlConflict(
                'Finish pending credit delivery before changing budgets'
            )
        settings = await self.settings()
        if (
            operation.org_id != self.org_id
            or operation.status != 'pending'
            or operation.generation != settings.control_generation
            or (operation.kind != 'adopt' and settings.control_mode != 'managed')
            or (
                operation.kind == 'adopt'
                and settings.control_mode not in {'external', 'needs_adoption'}
            )
        ):
            raise BudgetWriteDenied('Budget operation no longer has write authority')
        if self._uncommitted_budget_state:
            raise BudgetWriteDenied('Budget operation intent is not durable')
        # Track flush versus commit without borrowing a second connection from a saturated pool.
        with self.session.no_autoflush:
            persisted = (
                await self.session.execute(
                    select(
                        OrgBudgetOperation.plan,
                        OrgBudgetOperation.status,
                        OrgBudgetOperation.generation,
                        OrgBudgetOperation.kind,
                        OrgBudgetSettings.control_mode,
                        OrgBudgetSettings.control_generation,
                    )
                    .join(
                        OrgBudgetSettings,
                        OrgBudgetSettings.org_id == OrgBudgetOperation.org_id,
                    )
                    .where(
                        OrgBudgetOperation.id == operation.id,
                        OrgBudgetOperation.org_id == self.org_id,
                    )
                )
            ).one_or_none()
        if (
            persisted is None
            or persisted.status != 'pending'
            or persisted.plan != operation.plan
            or persisted.kind != operation.kind
            or persisted.generation != operation.generation
            or persisted.control_generation != operation.generation
            or persisted.control_mode != settings.control_mode
        ):
            raise BudgetWriteDenied('Budget operation intent is not durable')

    async def finish_operation(
        self,
        operation: OrgBudgetOperation,
        apply_settings: Callable[[OrgBudgetSettings], Awaitable[None]] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        await self.require_executable(operation)
        settings = await self.settings()
        if apply_settings is not None:
            await apply_settings(settings)
        operation.status = 'applied'
        operation.verification = verification
        operation.finished_at = datetime.now(UTC)
        operation.last_error = None
        if operation.kind == 'adopt':
            settings.control_mode = 'managed'
            settings.control_changed_at = operation.finished_at
            settings.control_changed_by = operation.actor
        await self.session.commit()

    async def record_failure(self, operation: OrgBudgetOperation, error: str) -> None:
        await self.require_executable(operation)
        operation.last_error = error[:500]
        await self.session.commit()

    async def authorize_write(
        self, operation_id: UUID, path: str, body: dict[str, Any]
    ) -> None:
        self.assert_locked()
        operation = await self.session.get(OrgBudgetOperation, operation_id)
        if operation is None:
            raise BudgetWriteDenied('Budget operation does not exist')
        await self.require_executable(operation)
        if body.get('team_id') != str(self.org_id) or path not in {
            '/team/update',
            '/team/member_update',
            '/team/member_add',
        }:
            raise BudgetWriteDenied(
                'Budget operation cannot write outside its organization'
            )
        requested = budget_request_hash({'path': path, 'body': body})
        if not any(
            budget_request_hash(write) == requested
            for write in operation.plan.get('writes', [])
        ):
            raise BudgetWriteDenied(
                'Budget mutation is not part of the committed operation'
            )

    async def hand_off(self, actor: str) -> None:
        if not actor:
            raise ValueError('An actor is required')
        if await self.pending_credit() is not None:
            raise BudgetControlConflict(
                'Finish pending credit delivery before handing off budgets'
            )
        settings = await self.settings()
        pending = await self.pending_operation()
        if pending is not None and pending.plan.get('revoked_member_ids'):
            raise BudgetControlConflict(
                'Finish pending member revocation before handing off budgets'
            )
        if pending is not None:
            pending.status = 'abandoned'
            pending.finished_at = datetime.now(UTC)
        settings.control_generation += 1
        settings.control_mode = 'external'
        settings.control_changed_at = datetime.now(UTC)
        settings.control_changed_by = actor
        # Handoff never restores old caps: spend may have advanced since they were saved.
        await self.session.commit()


_current_control: ContextVar[BudgetControlSession | None] = ContextVar(
    'budget_control_session', default=None
)


def current_budget_control(org_id: UUID) -> BudgetControlSession:
    control = _current_control.get()
    if control is None or control.org_id != org_id:
        raise BudgetWriteDenied('A shared organization budget lock is required')
    control.assert_locked()
    return control


@asynccontextmanager
async def budget_control_session(
    engine: AsyncEngine, org_id: UUID
) -> AsyncIterator[BudgetControlSession]:
    existing = _current_control.get()
    if existing is not None:
        existing.assert_locked()
        if existing.org_id != org_id:
            raise BudgetControlConflict(
                'Nested budget locks must target the same organization'
            )
        yield existing
        return
    async with engine.connect() as connection:
        locked = False
        try:
            locked = bool(
                await connection.scalar(
                    text('SELECT pg_try_advisory_lock(:key)'),
                    {'key': _lock_key(org_id)},
                )
            )
            await connection.commit()
            if not locked:
                raise BudgetControlConflict(
                    'An organization budget change is already in progress'
                )
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                control = BudgetControlSession(org_id, session, asyncio.current_task())
                token = _current_control.set(control)
                try:
                    yield control
                finally:
                    control.active = False
                    _current_control.reset(token)
        finally:
            try:
                await connection.rollback()
                released = await connection.scalar(
                    text('SELECT pg_advisory_unlock(:key)'), {'key': _lock_key(org_id)}
                )
                await connection.commit()
                if locked and not released:
                    raise BudgetWriteDenied('Organization budget lock was lost')
            except BaseException:
                # Also covers cancellation after PostgreSQL acquired the lock but before its response.
                await connection.invalidate()
                raise
