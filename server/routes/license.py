"""The license status the frontend shows in its license banner."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from openhands.app_server.utils.dependencies import get_dependencies
from server.licensing import get_license_service
from server.licensing.service import LicenseState
from server.licensing.verification import (
    InvalidLicensePayload,
    ValidLicensePayload,
)

license_router = APIRouter(
    prefix='/api/license', tags=['License'], dependencies=get_dependencies()
)


class LicenseStatusResponse(BaseModel):
    state: LicenseState
    # Why the license is not valid, when state is invalid.
    reason: str | None = None
    plan: str | None = None
    # The payload's status.state, such as trial_active, when state is valid.
    subscription_state: str | None = None
    trial_ends_at: datetime | None = None
    overdue_since: datetime | None = None
    expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    customer_center_url: str


@license_router.get('/status', response_model=LicenseStatusResponse)
async def get_license_status() -> LicenseStatusResponse:
    service = get_license_service()
    current = service.status()
    response = LicenseStatusResponse(
        state=current.state,
        last_checked_at=current.last_checked_at,
        customer_center_url=service.config.license_server_url,
    )
    payload = current.payload
    if isinstance(payload, ValidLicensePayload):
        response.plan = payload.plan
        response.subscription_state = payload.status.state
        response.trial_ends_at = payload.status.trial_ends_at
        response.overdue_since = payload.status.overdue_since
        response.expires_at = payload.expires_at
    elif isinstance(payload, InvalidLicensePayload):
        response.reason = payload.reason
        response.expires_at = payload.expires_at
    return response
