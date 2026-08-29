"""Read-only daily conversation quota status and enforcement for SaaS."""

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
from storage.daily_conversation_usage import DailyConversationUsage
from storage.org import Org
from storage.user import User

DEFAULT_ENV_VAR = 'OH_DAILY_CONVERSATION_LIMIT'
EXEMPT_LIMIT = -1


def configured_daily_limit() -> int | None:
    """Read the deployment default; unset and blank mean unlimited."""
    raw = os.getenv(DEFAULT_ENV_VAR)
    if raw is None or not raw.strip():
        return None
    return int(raw)


class QuotaStatus(BaseModel):
    """Current daily quota snapshot for the settings page."""

    daily_limit: int | None
    used_today: int
    remaining: int | None
    reset_at: str


@dataclass
class DailyConversationQuotaService:
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
        """Resolve the effective daily conversation limit.

        Precedence (first non-None wins):
        1. User-level override (``user.daily_conversation_limit``)
        2. Org-level override (``org.daily_conversation_limit``)
        3. Deployment default (``OH_DAILY_CONVERSATION_LIMIT`` env var)

        NULL at any level means "inherit from the next level down."
        A NULL org override means the org is exempt (unlimited) — paying
        SaaS orgs can have this set to NULL to bypass quota enforcement.
        """
        user = await self.db_session.scalar(
            select(User).where(User.id == UUID(user_id))
        )
        if user is not None and user.daily_conversation_limit is not None:
            return (
                None
                if user.daily_conversation_limit == EXEMPT_LIMIT
                else user.daily_conversation_limit
            )

        return await self.get_default_limit(
            org_id or (user.current_org_id if user else None)
        )

    async def get_default_limit(self, org_id: UUID | None) -> int | None:
        """Resolve the org-level limit and deployment default."""
        if org_id is not None:
            org = await self.db_session.scalar(select(Org).where(Org.id == org_id))
            if org is not None and org.daily_conversation_limit is not None:
                return (
                    None
                    if org.daily_conversation_limit == EXEMPT_LIMIT
                    else org.daily_conversation_limit
                )
        return configured_daily_limit()

    async def reserve(self, user_id: str, org_id: UUID | None = None) -> bool:
        """Atomically increment today's usage, raising HTTP 429 if at limit."""
        limit = await self.get_limit(user_id, org_id)
        if limit is None:
            return False

        today = datetime.now(UTC).date()
        if limit <= 0:
            used = await self._used(user_id, today)
            raise self._limit_reached(limit, used, today)

        now = datetime.now(UTC)
        statement = (
            insert(DailyConversationUsage)
            .values(
                user_id=UUID(user_id),
                usage_date=today,
                conversation_count=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=['user_id', 'usage_date'],
                set_={
                    'conversation_count': DailyConversationUsage.conversation_count + 1,
                    'updated_at': now,
                },
                where=DailyConversationUsage.conversation_count < limit,
            )
            .returning(DailyConversationUsage.conversation_count)
        )
        count = (await self.db_session.execute(statement)).scalar_one_or_none()
        if count is None:
            await self.db_session.rollback()
            used = await self._used(user_id, today)
            raise self._limit_reached(limit, used, today)
        await self.db_session.commit()
        return True

    async def release(self, user_id: str) -> None:
        """Release a reservation when the start request was not accepted."""
        today = datetime.now(UTC).date()
        await self.db_session.execute(
            text(
                'UPDATE daily_conversation_usage '
                'SET conversation_count = GREATEST(conversation_count - 1, 0), '
                'updated_at = CURRENT_TIMESTAMP '
                'WHERE user_id = :user_id AND usage_date = :usage_date'
            ),
            {'user_id': UUID(user_id), 'usage_date': today},
        )
        await self.db_session.commit()

    @staticmethod
    def _limit_reached(limit: int, used: int, usage_date: date) -> HTTPException:
        reset_at = datetime.combine(
            usage_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                'code': 'daily_conversation_limit_reached',
                'message': (
                    f"Daily conversation limit of {limit} reached. "
                    "Request a quota increase at /settings/quota."
                ),
                'limit': limit,
                'used': used,
                'reset_at': reset_at.isoformat(),
            },
        )

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
            select(DailyConversationUsage).where(
                DailyConversationUsage.user_id == UUID(user_id),
                DailyConversationUsage.usage_date == usage_date,
            )
        )
        return usage.conversation_count if usage else 0
