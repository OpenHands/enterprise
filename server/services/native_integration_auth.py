"""Bind ancillary provider OAuth to the authenticated native browser session."""

from collections.abc import Mapping
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.authorization import (
    Permission,
    get_user_org_role,
    get_user_super_role,
    has_permission,
)
from server.auth.native_session import SESSION_COOKIE
from server.services.native_auth_service import get_native_auth_service


async def native_integration_session(request: Request, account_id: str) -> str:
    # Explicit credentials are validated even on callbacks outside /api. They
    # must refer to the cookie identity whose browser session owns this flow.
    auth = await get_user_auth(request)
    principal = await get_native_auth_service().authenticate_session(
        request.cookies.get(SESSION_COOKIE, '')
    )
    if (
        principal is None
        or str(principal.account_id) != account_id
        or await auth.get_user_id() != account_id
    ):
        raise HTTPException(
            403, 'Integration linking requires the same signed-in browser account'
        )
    return str(principal.session_id)


class NativeIntegrationState(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    keycloak_user_id: str | None = None
    native_session_id: str | None = None
    operation_type: str | None = None
    org_id: str | None = None


async def verify_native_integration_session(
    request: Request, state: Mapping[str, JsonValue]
) -> None:
    try:
        identity = NativeIntegrationState.model_validate(state)
    except ValidationError as exc:
        raise HTTPException(403, 'Integration session is no longer valid') from exc
    account_id = identity.keycloak_user_id
    if not account_id or identity.native_session_id != await native_integration_session(
        request, account_id
    ):
        raise HTTPException(403, 'Integration session is no longer valid')
    if identity.operation_type == 'workspace_integration':
        org_id = UUID(identity.org_id) if identity.org_id else None
        org_role = await get_user_org_role(account_id, org_id)
        global_role = await get_user_super_role(account_id)
        permission = Permission.MANAGE_INTEGRATION_PROVIDERS
        if not (
            (org_role and has_permission(org_role, permission))
            or (global_role and has_permission(global_role, permission, is_super=True))
        ):
            raise HTTPException(
                403, 'Integration management permission is no longer available'
            )
