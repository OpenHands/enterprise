"""Durable, owner-scoped inventory for app-managed sandbox providers.

Every write uses its own short transaction. A successful external allocation must
not disappear when an unrelated conversation/request transaction rolls back.
Secrets, including the private launch snapshot, are encrypted explicitly rather
than relying on the legacy SQL secret decorator's result hook.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import JSON, String, Text, false, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import Select

from openhands.agent_server.utils import utc_now
from openhands.app_server.errors import PermissionsError, SandboxError
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.sandbox.sandbox_provider_config import DockerLaunchSpec
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.sql_utils import Base, UtcDateTime


@dataclass(frozen=True)
class _OperationConnection:
    store: SQLSandboxStore
    task: asyncio.Task[object] | None
    connection: AsyncConnection


_operation_connection: ContextVar[_OperationConnection | None] = ContextVar(
    'managed_sandbox_operation_connection', default=None
)


def hash_session_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class StoredManagedSandbox(Base):
    __tablename__ = 'v1_managed_sandbox'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sandbox_spec_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    application_id: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    container_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    initializer_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    owned_volume_names: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    launch_spec_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    session_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    session_api_key_hash: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    desired_state: Mapped[str] = mapped_column(String, nullable=False, index=True)
    initialized_generation: Mapped[str | None] = mapped_column(String, nullable=True)
    initializing_generation: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())


@dataclass(frozen=True)
class ManagedSandboxCredentials:
    session_key: SecretStr = field(repr=False)
    workspace_key: SecretStr = field(repr=False)


class _LaunchSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    launch: DockerLaunchSpec
    cors_origins: list[str]


@dataclass
class SQLSandboxStore:
    engine: AsyncEngine = field(repr=False)
    jwt_service: JwtService = field(repr=False)
    owner_id: str | None
    is_admin: bool = False

    def _select(self) -> Select[tuple[StoredManagedSandbox]]:
        stmt = select(StoredManagedSandbox).where(
            StoredManagedSandbox.provider == 'docker'
        )
        if not self.is_admin:
            stmt = stmt.where(
                StoredManagedSandbox.created_by_user_id == self.owner_id
                if self.owner_id
                else false()
            )
        return stmt

    def _assert_owner(self, row: StoredManagedSandbox) -> None:
        if row.provider != 'docker' or not (
            self.is_admin or (self.owner_id and row.created_by_user_id == self.owner_id)
        ):
            raise PermissionsError('Sandbox access denied')

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        connection = self._current_connection()
        return async_sessionmaker(
            connection if connection is not None else self.engine,
            expire_on_commit=False,
        )

    def _current_connection(self) -> AsyncConnection | None:
        operation = _operation_connection.get()
        # ContextVars propagate to child tasks; SQLAlchemy connections must not.
        if (
            operation
            and operation.store is self
            and operation.task is asyncio.current_task()
        ):
            return operation.connection
        return None

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        existing = self._current_connection()
        if existing is not None:
            yield existing
            return
        async with self.engine.connect() as connection:
            token = _operation_connection.set(
                _OperationConnection(self, asyncio.current_task(), connection)
            )
            try:
                yield connection
            finally:
                _operation_connection.reset(token)

    async def get(self, sandbox_id: str) -> StoredManagedSandbox | None:
        async with self._sessions()() as session:
            result = await session.scalars(
                self._select().where(StoredManagedSandbox.id == sandbox_id)
            )
            return result.one_or_none()

    async def get_many(self, ids: list[str]) -> list[StoredManagedSandbox]:
        if not ids:
            return []
        async with self._sessions()() as session:
            result = await session.scalars(
                self._select().where(StoredManagedSandbox.id.in_(set(ids)))
            )
            return list(result)

    async def page(self, offset: int, limit: int) -> list[StoredManagedSandbox]:
        async with self._sessions()() as session:
            result = await session.scalars(
                self._select()
                .order_by(
                    StoredManagedSandbox.created_at.desc(), StoredManagedSandbox.id
                )
                .offset(offset)
                .limit(limit)
            )
            return list(result)

    async def active_for_owner(
        self, owner_id: str, excluding: str | None = None
    ) -> list[StoredManagedSandbox]:
        async with self._sessions()() as session:
            stmt = self._select().where(
                StoredManagedSandbox.created_by_user_id == owner_id,
                # Failed operations can still own running resources. Count
                # them until pause/delete positively completes.
                StoredManagedSandbox.desired_state.in_(
                    ('running', 'starting', 'error', 'cleanup', 'deleting')
                ),
            )
            if excluding is not None:
                stmt = stmt.where(StoredManagedSandbox.id != excluding)
            result = await session.scalars(
                stmt.order_by(StoredManagedSandbox.created_at, StoredManagedSandbox.id)
            )
            return list(result)

    async def by_key(self, key: str) -> StoredManagedSandbox | None:
        async with self._sessions()() as session:
            result = await session.scalars(
                self._select().where(
                    StoredManagedSandbox.session_api_key_hash == hash_session_key(key),
                    StoredManagedSandbox.desired_state == 'running',
                )
            )
            return result.one_or_none()

    async def record_by_key(self, key: str) -> SandboxRecord | None:
        row = await self.by_key(key)
        return (
            SandboxRecord(id=row.id, created_by_user_id=row.created_by_user_id)
            if row
            else None
        )

    async def insert(self, row: StoredManagedSandbox) -> None:
        self._assert_owner(row)
        async with self._sessions()() as session, session.begin():
            session.add(row)

    async def save(self, row: StoredManagedSandbox) -> None:
        self._assert_owner(row)
        row.updated_at = utc_now()
        async with self._sessions()() as session, session.begin():
            await session.merge(row)

    async def remove(self, row: StoredManagedSandbox) -> None:
        self._assert_owner(row)
        async with self._sessions()() as session, session.begin():
            stored = await session.scalar(
                self._select().where(StoredManagedSandbox.id == row.id)
            )
            if stored:
                await session.delete(stored)

    def encrypt_launch(self, launch: DockerLaunchSpec, cors_origins: list[str]) -> str:
        return self.jwt_service.encrypt_value(
            _LaunchSnapshot(launch=launch, cors_origins=cors_origins).model_dump_json(
                context={'expose_secrets': True}
            )
        )

    def launch(self, row: StoredManagedSandbox) -> tuple[DockerLaunchSpec, list[str]]:
        self._assert_owner(row)
        snapshot = _LaunchSnapshot.model_validate_json(
            self.jwt_service.decrypt_value(row.launch_spec_ciphertext)
        )
        return snapshot.launch, snapshot.cors_origins

    def credentials(self, row: StoredManagedSandbox) -> ManagedSandboxCredentials:
        self._assert_owner(row)
        return ManagedSandboxCredentials(
            session_key=SecretStr(
                self.jwt_service.decrypt_value(row.session_key_ciphertext)
            ),
            workspace_key=SecretStr(
                self.jwt_service.decrypt_value(row.workspace_key_ciphertext)
            ),
        )

    @asynccontextmanager
    async def lock(self, name: str) -> AsyncIterator[None]:
        """Serialize one bounded manual operation, without a transaction across I/O.

        Session advisory locks release if the worker dies. Durable state and
        deterministic resource names allow the next operation to recover. A busy
        operation returns 503 instead of queuing overlapping key rotations.
        This needs a direct PostgreSQL connection or session-mode pooler.
        """
        if self.engine.dialect.name != 'postgresql':
            raise SandboxError('Managed Docker requires PostgreSQL')
        key = int.from_bytes(
            hashlib.sha256(f'managed-sandbox:{name}'.encode()).digest()[:8],
            'big',
            signed=True,
        )
        # Nested locks and CRUD share one connection even with pool_size=1.
        # Every statement/CRUD transaction ends before provider I/O; only the
        # session advisory lock remains held across the bounded operation.
        async with self._connection() as connection:
            acquired = bool(
                await connection.scalar(
                    text('SELECT pg_try_advisory_lock(:key)'), {'key': key}
                )
            )
            await connection.commit()
            if not acquired:
                raise SandboxError(
                    'Another sandbox operation is in progress. Please retry.',
                    status_code=503,
                )
            try:
                yield
            finally:

                async def release() -> None:
                    try:
                        await connection.execute(
                            text('SELECT pg_advisory_unlock(:key)'), {'key': key}
                        )
                        await connection.commit()
                    except BaseException:
                        # Never return a connection with a session lock to the pool.
                        await connection.invalidate()
                        raise

                task = asyncio.create_task(release())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
