"""Read-only daily conversation quota status for the authenticated user."""

from __future__ import annotations

import os
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

# Sentinel stored in ``user.daily_conversation_limit`` /
# ``org.daily_conversation_limit`` meaning "exempt -- no limit at all".
# NULL cannot carry that meaning because NULL already means "inherit from
# the next level down".
EXEMPT_LIMIT = -1
QUOTA_INCREASE_REQUEST_URL = 'https://u8mk1.share.hsforms.com/2lXOvoRtHRfeWEmba8CdOGw'


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

    async def get_status(self, user_id: str, org_id: UUID) -> QuotaStatus:
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

    async def get_limit(self, user_id: str, org_id: UUID) -> int | None:
        """Resolve the effective daily conversation limit, or None for unlimited.

        Precedence (first non-NULL level wins):

        1. User-level override (``user.daily_conversation_limit``)
        2. Org-level override (``org.daily_conversation_limit``)
        3. Deployment default (``OH_DAILY_CONVERSATION_LIMIT`` env var)

        NULL at a level means "inherit from the next level down"; it never
        means exempt. To exempt (e.g. a paying SaaS org) store
        ``EXEMPT_LIMIT`` (-1), which resolves to None at either level.

        ``org_id`` must be the request's *effective* org -- resolved via
        ``EFFECTIVE_ORG_ID`` so the API-key binding and ``X-Org-Id`` header
        are honored. It is deliberately not derived from
        ``user.current_org_id`` here: that is only the user's last-selected
        org and would apply the wrong org's quota (or the wrong org's
        exemption) whenever the request is scoped to a different one.
        """
        user = await self.db_session.scalar(
            select(User).where(User.id == UUID(user_id))
        )
        if user is not None and user.daily_conversation_limit is not None:
            return self._resolve_sentinel(user.daily_conversation_limit)

        return await self.get_default_limit(org_id)

    async def get_default_limit(self, org_id: UUID) -> int | None:
        """Effective limit for ``org_id`` ignoring any user-level override.

        Resolves the org-level override, then the deployment default.
        Returns None when unlimited at this level (org exemption via
        ``EXEMPT_LIMIT``, or no deployment default configured). Quota
        increase requests cap against this value so self-service grants
        cannot compound on top of previously granted increases.
        """
        org = await self.db_session.scalar(select(Org).where(Org.id == org_id))
        if org is not None and org.daily_conversation_limit is not None:
            return self._resolve_sentinel(org.daily_conversation_limit)

        return configured_daily_limit()

    async def reserve(self, user_id: str, org_id: UUID) -> bool:
        """Atomically increment today's usage, raising HTTP 429 at the limit."""
        limit = await self.get_limit(user_id, org_id)
        if limit is None:
            return False
        today = datetime.now(UTC).date()
        if limit <= 0:
            raise self._limit_reached(limit, await self._used(user_id, today), today)
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
            raise self._limit_reached(limit, await self._used(user_id, today), today)
        await self.db_session.commit()
        return True

    async def release(self, user_id: str) -> None:
        """Release a reservation when conversation startup fails."""
        today = datetime.now(UTC).date()
        await self.db_session.execute(
            text(
                'UPDATE daily_conversation_usage SET conversation_count = GREATEST(conversation_count - 1, 0), updated_at = CURRENT_TIMESTAMP WHERE user_id = :user_id AND usage_date = :usage_date'
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
                    f'Daily conversation limit of {limit} reached. Request a quota increase at /settings/quota or {QUOTA_INCREASE_REQUEST_URL}'
                ),
                'limit': limit,
                'used': used,
                'reset_at': reset_at.isoformat(),
            },
        )

    @staticmethod
    def _resolve_sentinel(limit: int) -> int | None:
        """Translate the stored exemption sentinel into "unlimited"."""
        return None if limit == EXEMPT_LIMIT else limit

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
