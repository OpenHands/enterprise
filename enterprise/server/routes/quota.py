"""Quota status API for the settings page."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
)
from storage.database import a_session_maker

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.dependencies import get_dependencies

quota_router = APIRouter(
    prefix='/api/quota', tags=['Quota'], dependencies=get_dependencies()
)


class QuotaStatusResponse(BaseModel):
    daily_limit: int | None
    used_today: int
    remaining: int | None
    reset_at: str


@quota_router.get('/status', response_model=QuotaStatusResponse)
async def get_quota_status(user_id: str = Depends(get_user_id)) -> QuotaStatusResponse:
    """Return the authenticated user's daily conversation quota status."""
    async with a_session_maker() as session:
        service = DailyConversationQuotaService(session)
        status = await service.get_status(user_id)
    return QuotaStatusResponse(
        daily_limit=status.daily_limit,
        used_today=status.used_today,
        remaining=status.remaining,
        reset_at=status.reset_at,
    )
