"""Super-admin lookup of the users behind a set of sandboxes.

Sandbox and conversation routes are scoped to the calling user, so nobody can
see who owns a sandbox they did not start. This route resolves a batch of
sandbox ids to their owners, with each owner's recent sandbox and conversation
history. It is read-only and requires ``Permission.VIEW_SANDBOX_OWNERS``,
granted only to the ``superadmin`` super role.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.sandbox.sandbox_store import StoredSandbox
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from storage.database import a_session_maker
from storage.org import Org
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas
from storage.user import User

sandbox_owner_admin_router = APIRouter(prefix='/api/admin/sandboxes', tags=['Admin'])

MAX_SANDBOX_IDS = 100
CONVERSATIONS_PER_ENTRY = 10


class ConversationSummary(BaseModel):
    id: str
    sandbox_id: str | None
    title: str | None
    trigger: str | None
    selected_repository: str | None
    created_at: datetime | None
    deleted_at: datetime | None


class SandboxOwnersRequest(BaseModel):
    sandbox_ids: list[str] = Field(min_length=1, max_length=MAX_SANDBOX_IDS)


class SandboxEntry(BaseModel):
    sandbox_id: str
    created_at: datetime
    user_id: str | None
    credential_org_id: UUID | None
    byor_export_enabled: bool | None
    conversation_count: int
    recent_conversations: list[ConversationSummary]


class OwnerEntry(BaseModel):
    user_id: str
    email: str | None
    email_verified: bool | None
    first_login_at: datetime | None
    is_disabled: bool
    sandboxes_started_24h: int
    sandboxes_started_7d: int
    conversation_count: int
    recent_conversations: list[ConversationSummary]


class SandboxOwnersResponse(BaseModel):
    sandboxes: list[SandboxEntry]
    owners: dict[str, OwnerEntry]
    unknown_sandbox_ids: list[str]


def _summary(row) -> ConversationSummary:
    return ConversationSummary(
        id=row.conversation_id,
        sandbox_id=row.sandbox_id,
        title=row.title,
        trigger=row.trigger,
        selected_repository=row.selected_repository,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _conversation_columns():
    meta = StoredConversationMetadata
    return (
        meta.conversation_id,
        meta.sandbox_id,
        meta.title,
        meta.trigger,
        meta.selected_repository,
        meta.created_at,
        meta.deleted_at,
    )


async def _recent_by_sandbox(session, sandbox_ids: list[str]):
    meta = StoredConversationMetadata
    ranked = (
        select(
            *_conversation_columns(),
            func.row_number()
            .over(partition_by=meta.sandbox_id, order_by=meta.created_at.desc())
            .label('rank'),
            func.count().over(partition_by=meta.sandbox_id).label('total'),
        )
        .where(meta.sandbox_id.in_(sandbox_ids))
        .subquery()
    )
    return (
        await session.execute(
            select(ranked).where(ranked.c.rank <= CONVERSATIONS_PER_ENTRY)
        )
    ).all()


async def _recent_by_user(session, user_ids: list[UUID]):
    meta = StoredConversationMetadata
    saas = StoredConversationMetadataSaas
    ranked = (
        select(
            *_conversation_columns(),
            saas.user_id,
            func.row_number()
            .over(partition_by=saas.user_id, order_by=meta.created_at.desc())
            .label('rank'),
            func.count().over(partition_by=saas.user_id).label('total'),
        )
        .join(saas, saas.conversation_id == meta.conversation_id)
        .where(saas.user_id.in_(user_ids))
        .subquery()
    )
    return (
        await session.execute(
            select(ranked).where(ranked.c.rank <= CONVERSATIONS_PER_ENTRY)
        )
    ).all()


async def _sandboxes_started(session, user_ids: list[UUID], now: datetime):
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    rows = await session.execute(
        select(
            StoredSandbox.created_by_user_id,
            func.count().filter(StoredSandbox.created_at >= day_ago),
            func.count(),
        )
        .where(
            StoredSandbox.created_by_user_id.in_([str(u) for u in user_ids]),
            StoredSandbox.created_at >= week_ago,
        )
        .group_by(StoredSandbox.created_by_user_id)
    )
    return {user_id: (day, week) for user_id, day, week in rows}


async def lookup_sandbox_owners(sandbox_ids: list[str]) -> SandboxOwnersResponse:
    now = datetime.now(UTC)
    async with a_session_maker() as session:
        sandboxes = (
            await session.execute(
                select(
                    StoredSandbox.id,
                    StoredSandbox.created_at,
                    StoredSandbox.created_by_user_id,
                    StoredSandbox.credential_org_id,
                ).where(StoredSandbox.id.in_(sandbox_ids))
            )
        ).all()
        known_ids = [sandbox.id for sandbox in sandboxes]
        known = set(known_ids)
        owner_ids = sorted(
            {UUID(s.created_by_user_id) for s in sandboxes if s.created_by_user_id}
        )
        org_ids = {s.credential_org_id for s in sandboxes if s.credential_org_id}

        byor_by_org = dict(
            (
                await session.execute(
                    select(Org.id, Org.byor_export_enabled).where(Org.id.in_(org_ids))
                )
            ).all()
        )
        users = {
            user.id: user
            for user in (
                await session.execute(select(User).where(User.id.in_(owner_ids)))
            ).scalars()
        }
        started = await _sandboxes_started(session, owner_ids, now)
        by_sandbox = await _recent_by_sandbox(session, known_ids)
        by_user = await _recent_by_user(session, owner_ids)

    sandbox_conversations: dict[str, list[ConversationSummary]] = {}
    sandbox_totals: dict[str, int] = {}
    for row in by_sandbox:
        sandbox_conversations.setdefault(row.sandbox_id, []).append(_summary(row))
        sandbox_totals[row.sandbox_id] = row.total

    owner_conversations: dict[UUID, list[ConversationSummary]] = {}
    owner_totals: dict[UUID, int] = {}
    for row in by_user:
        owner_conversations.setdefault(row.user_id, []).append(_summary(row))
        owner_totals[row.user_id] = row.total

    owners = {}
    for user_id, user in users.items():
        day, week = started.get(str(user_id), (0, 0))
        owners[str(user_id)] = OwnerEntry(
            user_id=str(user_id),
            email=user.email,
            email_verified=user.email_verified,
            first_login_at=(
                user.first_login_at.replace(tzinfo=UTC) if user.first_login_at else None
            ),
            is_disabled=user.is_disabled,
            sandboxes_started_24h=day,
            sandboxes_started_7d=week,
            conversation_count=owner_totals.get(user_id, 0),
            recent_conversations=owner_conversations.get(user_id, []),
        )

    return SandboxOwnersResponse(
        sandboxes=[
            SandboxEntry(
                sandbox_id=s.id,
                created_at=s.created_at,
                user_id=s.created_by_user_id,
                credential_org_id=s.credential_org_id,
                byor_export_enabled=byor_by_org.get(s.credential_org_id),
                conversation_count=sandbox_totals.get(s.id, 0),
                recent_conversations=sandbox_conversations.get(s.id, []),
            )
            for s in sandboxes
        ],
        owners=owners,
        unknown_sandbox_ids=[i for i in sandbox_ids if i not in known],
    )


@sandbox_owner_admin_router.post('/owners')
async def get_sandbox_owners(
    body: SandboxOwnersRequest,
    caller_user_id: str = Depends(require_permission(Permission.VIEW_SANDBOX_OWNERS)),
) -> SandboxOwnersResponse:
    """Resolve sandbox ids to their owners and recent activity.

    A POST so the id list travels in the body rather than a query string. It
    changes nothing. Ids with no stored sandbox come back in
    ``unknown_sandbox_ids``. Requires ``VIEW_SANDBOX_OWNERS``.
    """
    sandbox_ids = list(dict.fromkeys(body.sandbox_ids))
    response = await lookup_sandbox_owners(sandbox_ids)
    logger.info(
        'sandbox_owners:lookup',
        extra={'caller_user_id': caller_user_id, 'sandbox_count': len(sandbox_ids)},
    )
    return response
