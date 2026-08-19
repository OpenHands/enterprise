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
        user = await self.db_session.scalar(
            select(User).where(User.id == UUID(user_id))
        )
        if user is None or user.daily_conversation_limit is None:
            return configured_daily_limit()
        return user.daily_conversation_limit

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
