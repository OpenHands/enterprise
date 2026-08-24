"""Quota status and org-level quota management API."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import EFFECTIVE_ORG_ID, REJECT_X_ORG_ID_PATH_MISMATCH
from server.services.daily_conversation_quota_service import (
    EXEMPT_LIMIT,
    DailyConversationQuotaService,
)
from sqlalchemy import select
from storage.database import a_session_maker
from storage.org import Org

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
async def get_quota_status(
    user_id: str = Depends(get_user_id),
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
) -> QuotaStatusResponse:
    """Return the authenticated user's daily conversation quota status.

    Scoped to the request's effective org (``X-Org-Id`` / API-key binding),
    so a user in several orgs sees the quota of the org they are actually
    working in.
    """
    async with a_session_maker() as session:
        service = DailyConversationQuotaService(session)
        quota = await service.get_status(user_id, effective_org_id)
    return QuotaStatusResponse(
        daily_limit=quota.daily_limit,
        used_today=quota.used_today,
        remaining=quota.remaining,
        reset_at=quota.reset_at,
    )


# --- Org-level quota management (admin) ---

quota_admin_router = APIRouter(
    prefix='/api/admin/quota',
    tags=['Admin'],
    dependencies=[*get_dependencies(), REJECT_X_ORG_ID_PATH_MISMATCH],
)


class OrgQuotaUpdateRequest(BaseModel):
    daily_conversation_limit: int | None = Field(
        description=(
            'NULL to inherit the deployment default, -1 to exempt the org '
            'entirely, or a positive integer for an org-specific limit.'
        ),
    )

    @field_validator('daily_conversation_limit')
    @classmethod
    def _reject_meaningless_limits(cls, value: int | None) -> int | None:
        """Allow only NULL, the exemption sentinel, and positive limits.

        0 and values below -1 are rejected rather than stored: they are not
        meaningful quotas, but they resolve to a limit the org can never
        satisfy, silently blocking every member with no error anywhere. A
        mistyped '-11' for '-1' would otherwise do the exact opposite of the
        intended exemption.
        """
        if value is None or value == EXEMPT_LIMIT or value > 0:
            return value
        raise ValueError(
            f'daily_conversation_limit must be null (inherit), {EXEMPT_LIMIT} '
            f'(exempt), or a positive integer; got {value}'
        )


class OrgQuotaResponse(BaseModel):
    org_id: str
    org_name: str
    daily_conversation_limit: int | None


@quota_admin_router.put('/orgs/{org_id}/quota', response_model=OrgQuotaResponse)
async def set_org_quota(
    org_id: UUID,
    body: OrgQuotaUpdateRequest,
    _: str = Depends(require_permission(Permission.MANAGE_ORG_QUOTA)),
) -> OrgQuotaResponse:
    """Set or clear an org-level daily conversation limit override.

    Requires the instance-level ``MANAGE_ORG_QUOTA`` permission, which is
    granted only to the superadmin super role. Going through
    ``require_permission`` (rather than checking the super role inline) also
    enforces the API-key organization binding, so a key bound to one org
    cannot edit another org's quota.

    Set to -1 to exempt the org entirely (unlimited).
    Set to NULL to inherit the deployment default.
    Set to a positive integer for an org-specific limit.
    """
    async with a_session_maker() as session:
        org = await session.scalar(select(Org).where(Org.id == org_id))
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
