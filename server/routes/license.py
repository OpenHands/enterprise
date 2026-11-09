"""The license status for the license banner, and the usage of each limit."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.dependencies import get_dependencies
from server.licensing import get_license_service
from server.licensing.limits import count_orgs, count_users, org_limit, user_limit
from server.licensing.service import LicenseState
from server.licensing.verification import (
    InvalidLicensePayload,
    ValidLicensePayload,
)
from storage.database import a_session_maker

license_router = APIRouter(
    prefix='/api/license', tags=['License'], dependencies=get_dependencies()
)


class LicenseFeaturesResponse(BaseModel):
    sso: bool


class LicenseStatusResponse(BaseModel):
    state: LicenseState
    # Why the license is not valid, when state is invalid.
    reason: str | None = None
    # Whether the install uses an offline license file.
    offline: bool = False
    # trial, paid or development.
    type: str | None = None
    # When the license expires. None when it never expires or the date isn't
    # known.
    license_ends_at: datetime | None = None
    expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    customer_center_url: str
    # What the install may use now. Every feature is on while enforcement is
    # off, and off without a valid license.
    features: LicenseFeaturesResponse


@license_router.get('/status', response_model=LicenseStatusResponse)
async def get_license_status() -> LicenseStatusResponse:
    service = get_license_service()
    current = service.status()
    response = LicenseStatusResponse(
        state=current.state,
        reason=current.reason,
        offline=service.config.offline and current.state != 'disabled',
        last_checked_at=current.last_checked_at,
        customer_center_url=service.config.license_server_url,
        features=LicenseFeaturesResponse(sso=service.has_feature('sso')),
    )
    payload = current.payload
    if isinstance(payload, ValidLicensePayload):
        response.type = payload.type
        response.license_ends_at = payload.license_ends_at
        response.expires_at = payload.expires_at
    elif isinstance(payload, InvalidLicensePayload):
        response.expires_at = payload.expires_at
    return response


class LimitUsage(BaseModel):
    used: int
    # None means unlimited.
    limit: int | None


class LicenseUsageResponse(BaseModel):
    users: LimitUsage
    # Team organizations. Personal workspaces don't count.
    orgs: LimitUsage
    customer_center_url: str


@license_router.get('/usage', response_model=LicenseUsageResponse)
async def get_license_usage(
    user_id: str | None = Depends(get_user_id),
) -> LicenseUsageResponse:
    """How much of each licensed limit the install uses."""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )
    async with a_session_maker() as session:
        users = await count_users(session)
        orgs = await count_orgs(session)
    return LicenseUsageResponse(
        users=LimitUsage(used=users, limit=user_limit()),
        orgs=LimitUsage(used=orgs, limit=org_limit()),
        customer_center_url=get_license_service().config.license_server_url,
    )
