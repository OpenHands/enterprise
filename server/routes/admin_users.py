"""Superadmin-only user lifecycle endpoints. Retry DELETE after a 503."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.auth.authorization import Permission, require_permission
from server.logger import logger
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
    LifecycleCleanupError,
)
from storage.user_data_cleanup import UserCleanupConflict

admin_user_router = APIRouter(prefix='/api/admin/users', tags=['Admin'])


class UserLifecycleResponse(BaseModel):
    user_id: str
    email: str | None = None


async def _operate(
    user_id: UUID, operation: str, caller_user_id: str
) -> UserLifecycleResponse:
    try:
        result = await getattr(AdminUserLifecycleService(), operation)(str(user_id))
    except (LastSuperAdminError, UserCleanupConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except LifecycleCleanupError as exc:
        raise HTTPException(503, str(exc)) from exc
    if result is None:
        raise HTTPException(404, 'User not found')
    logger.info(
        'admin_users:lifecycle',
        extra={
            'caller_user_id': caller_user_id,
            'target_user_id': str(user_id),
            'operation': operation,
        },
    )
    return UserLifecycleResponse(user_id=result.user_id, email=result.email)


@admin_user_router.post('/{user_id}/disable')
async def disable_user(
    user_id: UUID,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> UserLifecycleResponse:
    return await _operate(user_id, 'disable_user', caller_user_id)


@admin_user_router.post('/{user_id}/enable')
async def enable_user(
    user_id: UUID,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> UserLifecycleResponse:
    return await _operate(user_id, 'enable_user', caller_user_id)
