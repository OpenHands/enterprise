"""Read-only daily conversation quota status for the authenticated user."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from storage.daily_conversation_usage import DailyConversationUsage
from storage.user import User

DEFAULT_ENV_VAR = 'OH_DAILY_CONVERSATION_LIMIT'


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


class DailyConversationQuotaService:
    """Read-only quota status. Enforcement is added in a later stacked PR."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_status(self, user_id: str) -> QuotaStatus:
        limit = await self.get_limit(user_id)
        used = await self._used(user_id, datetime.now(UTC).date())
        remaining = None if limit is None else max(limit - used, 0)
        reset_at = self._next_reset_iso()
        return QuotaStatus(
            daily_limit=limit,
            used_today=used,
            remaining=remaining,
            reset_at=reset_at,
        )

    async def get_limit(self, user_id: str) -> int | None:
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
        if user is None:
            return configured_daily_limit()

        # User-level override takes precedence.
        if user.daily_conversation_limit is not None:
            return user.daily_conversation_limit

        return await self.get_default_limit(user)

    async def get_default_limit(self, user: User) -> int | None:
        """Effective limit ignoring any user-level override.

        Resolves the org-level override, then the deployment default.
        Returns None when the user is unlimited at this level (org
        exemption via -1, or no deployment default configured). Quota
        increase requests cap against this value so self-service grants
        cannot compound on top of previously granted increases.
        """
        # Org-level override: NULL means inherit deployment default,
        # -1 means exempt (unlimited, for paying SaaS orgs),
        # any other integer is the org-specific limit.
        if user.current_org_id is not None:
            from storage.org import Org

            org = await self.db_session.scalar(
                select(Org).where(Org.id == user.current_org_id)
            )
            if org is not None and org.daily_conversation_limit is not None:
                if org.daily_conversation_limit == -1:
                    return None  # exempt — unlimited
                return org.daily_conversation_limit

        return configured_daily_limit()

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
