"""Administrator-issued password recovery links."""

from uuid import UUID

from fastapi import APIRouter, Depends

from server.auth.authorization import Permission, require_permission
from server.auth.native_types import PasswordResetLink
from server.routes.native_auth import NativeAuthRoute
from server.services.native_password_service import get_native_password_service

MANAGE_USERS = require_permission(Permission.MANAGE_USERS)
auth_passwords_router = APIRouter(
    prefix='/api/admin', tags=['Admin'], route_class=NativeAuthRoute
)


@auth_passwords_router.post('/auth-accounts/{account_id}/password-reset')
async def issue_password_reset(
    account_id: UUID, user_id: str = Depends(MANAGE_USERS)
) -> PasswordResetLink:
    return await get_native_password_service().issue_password_reset(
        UUID(user_id), account_id
    )
