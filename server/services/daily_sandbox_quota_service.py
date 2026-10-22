"""Daily sandbox quota status and enforcement for SaaS.

This replaces the former daily *conversation* quota: the metered unit is now a
sandbox creation, because a sandbox is what allocates infrastructure (a runtime
pod / node). Conversations are no longer counted at all.

Limit resolution, exemptions, and persistence deliberately mirror the old
conversation quota so the settings page, admin tooling, and operator intuition
carry over:

1. User-level override (``user.daily_sandbox_limit``)
2. Org-level override (``org.daily_sandbox_limit``)
3. Deployment default (``OH_DAILY_SANDBOX_LIMIT`` env var)

NULL at any level means "inherit from the next level down." ``-1``
(``EXEMPT_LIMIT``) means explicitly exempt (unlimited).

Only sandbox *creation* on the remote backend is metered (see
``RemoteSandboxService._reserve_daily_sandbox_quota`` for the full scope
note). Resuming a paused sandbox is not metered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from storage.daily_sandbox_usage import DailySandboxUsage
from storage.org import Org
from storage.user import User

DEFAULT_ENV_VAR = 'OH_DAILY_SANDBOX_LIMIT'
LEGACY_ENV_VAR = 'OH_DAILY_CONVERSATION_LIMIT'
EXEMPT_LIMIT = -1
QUOTA_INCREASE_REQUEST_URL = 'https://u8mk1.share.hsforms.com/2lXOvoRtHRfeWEmba8CdOGw'


def configured_daily_limit() -> int | None:
    """Read the deployment default; unset and blank mean unlimited.

    Falls back to the legacy conversation-limit variable so existing
    deployments keep their configured value (20 in prod) without a chart
    change at rollout.
    """
    raw = os.getenv(DEFAULT_ENV_VAR)
    if raw is None or not raw.strip():
        raw = os.getenv(LEGACY_ENV_VAR)
    if raw is None or not raw.strip():
        return None
    return int(raw)


class QuotaStatus(BaseModel):
    """Current daily quota snapshot for the settings page."""

    daily_limit: int | None
    used_today: int
    remaining: int | None
    reset_at: str


class DailySandboxLimitReached(HTTPException):
    def __init__(self, limit: int, used: int, reset_at: datetime):
        self.limit = limit
        self.used = used
        self.reset_at = reset_at
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                'code': 'daily_sandbox_limit_reached',
                'error': 'DAILY_SANDBOX_LIMIT_REACHED',
                'message': f'Daily sandbox limit of {limit} reached. Request a quota increase at /settings/quota or {QUOTA_INCREASE_REQUEST_URL}',
                'limit': limit,
                'used': used,
                'reset_at': reset_at.isoformat(),
            },
        )


@dataclass
class DailySandboxQuotaService:
    """Quota status and enforcement. Enforcement is gated by a configured limit."""

    db_session: AsyncSession

    async def get_status(self, user_id: str, org_id: UUID | None = None) -> QuotaStatus:
        limit = await self.get_limit(user_id, org_id)
        used = await self._used(user_id, datetime.now(UTC).date())
        remaining = None if limit is None else max(limit - used, 0)
        reset_at = self._next_reset_iso()
        return QuotaStatus(
            daily_limit=limit,
            used_today=used,
            remaining=remaining,
            reset_at=reset_at,
        )

    async def get_limit(self, user_id: str, org_id: UUID | None = None) -> int | None:
        """Resolve the effective daily sandbox limit.

        Precedence (first non-None wins):
        1. User-level override (``user.daily_sandbox_limit``)
        2. Org-level override (``org.daily_sandbox_limit``)
        3. Deployment default (``OH_DAILY_SANDBOX_LIMIT`` env var)

        NULL at any level means "inherit from the next level down."
        """
        user = await self.db_session.scalar(
            select(User).where(User.id == UUID(user_id))
        )
        if user is not None and user.daily_sandbox_limit is not None:
            return (
                None
                if user.daily_sandbox_limit == EXEMPT_LIMIT
                else user.daily_sandbox_limit
            )

        return await self.get_default_limit(
            org_id or (user.current_org_id if user else None)
        )

    async def get_default_limit(self, org_id: UUID | None) -> int | None:
        """Resolve the org-level limit and deployment default."""
        if org_id is not None:
            org = await self.db_session.scalar(select(Org).where(Org.id == org_id))
            if org is not None and org.daily_sandbox_limit is not None:
                return (
                    None
                    if org.daily_sandbox_limit == EXEMPT_LIMIT
                    else org.daily_sandbox_limit
                )
        return configured_daily_limit()

    async def reserve(self, user_id: str, org_id: UUID | None = None) -> bool:
        """Atomically increment today's usage, raising HTTP 429 at the limit."""
        limit = await self.get_limit(user_id, org_id)
        if limit is None:
            return False
        today = datetime.now(UTC).date()
        if limit <= 0:
            raise self._limit_reached(limit, await self._used(user_id, today), today)
        now = datetime.now(UTC)
        statement = (
            insert(DailySandboxUsage)
            .values(
                user_id=UUID(user_id),
                usage_date=today,
                sandbox_count=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=['user_id', 'usage_date'],
                set_={
                    DailySandboxUsage.sandbox_count: (
                        DailySandboxUsage.sandbox_count + 1
                    ),
                    'updated_at': now,
                },
                where=DailySandboxUsage.sandbox_count < limit,
            )
            .returning(DailySandboxUsage.sandbox_count)
        )
        count = (await self.db_session.execute(statement)).scalar_one_or_none()
        if count is None:
            await self.db_session.rollback()
            raise self._limit_reached(limit, await self._used(user_id, today), today)
        await self.db_session.commit()
        return True

    async def release(self, user_id: str) -> None:
        """Release a reservation when sandbox startup fails."""
        today = datetime.now(UTC).date()
        await self.db_session.execute(
            text(
                'UPDATE daily_conversation_usage SET conversation_count = GREATEST(conversation_count - 1, 0), updated_at = CURRENT_TIMESTAMP WHERE user_id = :user_id AND usage_date = :usage_date'
            ),
            {'user_id': UUID(user_id), 'usage_date': today},
        )
        await self.db_session.commit()

    @staticmethod
    def _limit_reached(
        limit: int, used: int, usage_date: date
    ) -> DailySandboxLimitReached:
        reset_at = datetime.combine(
            usage_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        return DailySandboxLimitReached(limit, used, reset_at)

    @staticmethod
    def _next_reset_iso() -> str:
        """ISO timestamp of the next UTC midnight."""
        today = datetime.now(UTC).date()
        reset = datetime.combine(
            today + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        return reset.isoformat()

    async def _used(self, user_id: str, usage_date: date) -> int:
        usage = await self.db_session.scalar(
            select(DailySandboxUsage).where(
                DailySandboxUsage.user_id == UUID(user_id),
                DailySandboxUsage.usage_date == usage_date,
            )
        )
        return usage.sandbox_count if usage else 0
