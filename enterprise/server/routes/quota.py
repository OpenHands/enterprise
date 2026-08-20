"""Quota status and org-level quota management API."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
)
from sqlalchemy import select
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


# --- Org-level quota management (admin) ---

quota_admin_router = APIRouter(
    prefix='/api/admin/quota', tags=['Admin'], dependencies=get_dependencies()
)


class OrgQuotaUpdateRequest(BaseModel):
    daily_conversation_limit: int | None


class OrgQuotaResponse(BaseModel):
    org_id: str
    org_name: str
    daily_conversation_limit: int | None


async def _require_admin(user_id: str) -> None:
    """Check that the user is an admin (has superadmin role)."""
    from server.auth.authorization import get_user_super_role

    role = await get_user_super_role(user_id)
    if role is None or role.name != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required',
        )


@quota_admin_router.put('/orgs/{org_id}/quota', response_model=OrgQuotaResponse)
async def set_org_quota(
    org_id: str,
    body: OrgQuotaUpdateRequest,
    user_id: str = Depends(get_user_id),
) -> OrgQuotaResponse:
    """Set or clear an org-level daily conversation limit override.

    Admin only. Set to -1 to exempt the org entirely (unlimited).
    Set to NULL to inherit the deployment default.
    Set to a positive integer for an org-specific limit.
    """
    await _require_admin(user_id)
    from uuid import UUID

    from storage.org import Org

    async with a_session_maker() as session:
        org = await session.scalar(select(Org).where(Org.id == UUID(org_id)))
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Organization not found',
            )
        org.daily_conversation_limit = body.daily_conversation_limit
        await session.commit()

        return OrgQuotaResponse(
            org_id=str(org.id),
            org_name=org.name,
            daily_conversation_limit=org.daily_conversation_limit,
        )
