"""Instance-level user lifecycle administration endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from openhands.app_server.user_auth import get_user_auth
from server.auth.authorization import Permission, require_permission
from server.auth.contracts import AuthenticationUnavailable, Principal
from server.auth.user_management import (
    AccountPermissionError,
    EnterpriseUserManagementService,
)
from server.services.admin_user_lifecycle_service import LastSuperAdminError

admin_user_router = APIRouter(prefix='/api/admin/users', tags=['Admin'])


class UserLifecycleResponse(BaseModel):
    user_id: str
    email: str | None = None
    warnings: list[str] = Field(default_factory=list)


async def _operate(
    request: Request, operation: str, user_id: UUID
) -> UserLifecycleResponse:
    principal = getattr(await get_user_auth(request), 'principal', None)
    if not isinstance(principal, Principal):
        raise HTTPException(401, 'Authentication required')
    try:
        result = await EnterpriseUserManagementService().lifecycle(
            operation, user_id, actor=principal
        )
    except LastSuperAdminError as exc:
        raise HTTPException(409, str(exc)) from exc
    except AccountPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            503, 'Account update is temporarily unavailable. Retry the operation.'
        ) from exc
    if result is None:
        raise HTTPException(404, 'User not found')
    return UserLifecycleResponse(
        user_id=result.user_id, email=result.email, warnings=result.cleanup_warnings
    )


@admin_user_router.post('/{user_id}/disable', response_model=UserLifecycleResponse)
async def disable_user(
    user_id: UUID,
    request: Request,
    _: str = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserLifecycleResponse:
    return await _operate(request, 'disable', user_id)


@admin_user_router.post('/{user_id}/enable', response_model=UserLifecycleResponse)
async def enable_user(
    user_id: UUID,
    request: Request,
    _: str = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserLifecycleResponse:
    return await _operate(request, 'enable', user_id)


@admin_user_router.delete('/{user_id}', response_model=UserLifecycleResponse)
async def delete_user(
    user_id: UUID,
    request: Request,
    _: str = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserLifecycleResponse:
    return await _operate(request, 'delete', user_id)
