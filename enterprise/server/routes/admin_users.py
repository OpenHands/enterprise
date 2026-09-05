"""Instance-level user lifecycle administration endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from server.auth.authorization import Permission, require_permission
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
    UserDeletionResult,
    UserLifecycleResult,
)

admin_user_router = APIRouter(prefix='/api/admin/users', tags=['Admin'])


class UserLifecycleResponse(BaseModel):
    """Identity returned after a lifecycle operation."""

    user_id: str
    email: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _response(result: UserLifecycleResult) -> UserLifecycleResponse:
    return UserLifecycleResponse(user_id=result.user_id, email=result.email)


def _deletion_response(result: UserDeletionResult) -> UserLifecycleResponse:
    return UserLifecycleResponse(
        user_id=result.user_id,
        email=result.email,
        warnings=result.cleanup_warnings,
    )


@admin_user_router.post('/{user_id}/disable', response_model=UserLifecycleResponse)
async def disable_user(
    user_id: str,
    _=Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserLifecycleResponse:
    try:
        result = await AdminUserLifecycleService().disable_user(user_id)
    except LastSuperAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )
    return _response(result)


@admin_user_router.post('/{user_id}/enable', response_model=UserLifecycleResponse)
async def enable_user(
    user_id: str,
    _=Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserLifecycleResponse:
    result = await AdminUserLifecycleService().enable_user(user_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )
    return _response(result)


@admin_user_router.delete('/{user_id}', response_model=UserLifecycleResponse)
async def delete_user(
    user_id: str,
    _=Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserLifecycleResponse:
    try:
        result = await AdminUserLifecycleService().delete_user(user_id)
    except LastSuperAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )
    return _deletion_response(result)
