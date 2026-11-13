"""The license status for the license banner, the usage of each limit, and the
license a Super Admin saves."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.licensing import get_license_service
from server.licensing.config import LicenseConfig, load_license_config
from server.licensing.limits import count_orgs, count_users, org_limit, user_limit
from server.licensing.service import LicenseState
from server.licensing.verification import (
    InvalidLicensePayload,
    LicenseVerificationError,
    ValidLicensePayload,
    read_offline_license,
)
from storage.database import a_session_maker
from storage.instance_settings_store import InstanceSettingsStore

license_router = APIRouter(
    prefix='/api/license', tags=['License'], dependencies=get_dependencies()
)

LICENSE_KEY_PREFIX = 'ohe_lic_'
# The prefix and the first 8 random characters, which is what Customer Center
# shows of a key.
KEY_HINT_LENGTH = 16
MAX_OFFLINE_LICENSE_CHARS = 64 * 1024


class LicenseFeaturesResponse(BaseModel):
    sso: bool
    worklogs: bool


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
    # A Super Admin may have saved a license on another replica.
    if await service.reload():
        await service.check_now()
    current = service.status()
    response = LicenseStatusResponse(
        state=current.state,
        reason=current.reason,
        offline=service.config.offline and current.state != 'disabled',
        last_checked_at=current.last_checked_at,
        customer_center_url=service.config.license_server_url,
        features=LicenseFeaturesResponse(
            sso=service.has_feature('sso'),
            worklogs=service.has_feature('worklogs'),
        ),
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


class ConfiguredLicenseResponse(BaseModel):
    """The license this install uses, without its secret parts."""

    # Where the license is set: in the install's environment, or saved by a
    # Super Admin. None without a license.
    source: Literal['environment', 'saved'] | None = None
    kind: Literal['key', 'offline'] | None = None
    # The start of the license key, as Customer Center shows it.
    key_hint: str | None = None


class LicenseUpdate(BaseModel):
    """A license key, or the contents of an offline license file."""

    license_key: str | None = Field(default=None, max_length=255)
    offline_license: str | None = Field(
        default=None, max_length=MAX_OFFLINE_LICENSE_CHARS
    )


async def _configured_license() -> ConfiguredLicenseResponse:
    config = await load_license_config()
    if not config.configured:
        return ConfiguredLicenseResponse()
    source: Literal['environment', 'saved'] = (
        'environment' if LicenseConfig.from_env().configured else 'saved'
    )
    if config.offline or config.license_key is None:
        return ConfiguredLicenseResponse(source=source, kind='offline')
    return ConfiguredLicenseResponse(
        source=source, kind='key', key_hint=config.license_key[:KEY_HINT_LENGTH]
    )


@license_router.get('', response_model=ConfiguredLicenseResponse)
async def get_license(
    _: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> ConfiguredLicenseResponse:
    """The license this install uses. Requires ``MANAGE_SUPER_ADMINS``."""
    return await _configured_license()


@license_router.put('', response_model=ConfiguredLicenseResponse)
async def save_license(
    body: LicenseUpdate,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> ConfiguredLicenseResponse:
    """Save a license in place of the one saved before. Requires ``MANAGE_SUPER_ADMINS``.

    A license in the environment wins over a saved one, so an install that
    has one refuses the save.
    """
    if LicenseConfig.from_env().configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This install's license is set in its environment. Change it there.",
        )
    license_key = (body.license_key or '').strip() or None
    offline_license = (body.offline_license or '').strip() or None
    if (license_key is None) == (offline_license is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Enter a license key or choose an offline license file.',
        )
    if license_key is not None and not license_key.startswith(LICENSE_KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'A license key starts with {LICENSE_KEY_PREFIX}.',
        )
    if offline_license is not None:
        try:
            read_offline_license(json.loads(offline_license))
        except (ValueError, LicenseVerificationError) as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='This is not a valid offline license file.',
            ) from e

    await InstanceSettingsStore.save_license(
        license_key=license_key, offline_license=offline_license
    )
    service = get_license_service()
    if await service.reload():
        await service.check_now()
    logger.info(
        'admin:license:update',
        extra={
            'caller_user_id': caller_user_id,
            'kind': 'key' if license_key is not None else 'offline',
        },
    )
    return await _configured_license()
