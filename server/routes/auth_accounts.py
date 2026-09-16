"""Read-only account metadata for administration."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from server.auth.authorization import Permission, require_permission
from server.auth.native_types import NativeAccountMetadata, NativeAccountPage
from server.routes.native_auth import NO_STORE, NativeAuthRoute
from server.services.native_account_admin_service import (
    get_native_account_admin_service,
)

MANAGE_USERS = require_permission(Permission.MANAGE_USERS)
auth_accounts_router = APIRouter(
    prefix='/api/admin', tags=['Admin'], route_class=NativeAuthRoute
)


@auth_accounts_router.get('/auth-accounts')
async def list_accounts(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str = Depends(MANAGE_USERS),
) -> NativeAccountPage:
    return await get_native_account_admin_service().list_accounts(
        UUID(user_id), offset=offset, limit=limit
    )


@auth_accounts_router.get('/auth-accounts/{account_id}')
async def get_account(
    account_id: UUID, user_id: str = Depends(MANAGE_USERS)
) -> NativeAccountMetadata:
    result = await get_native_account_admin_service().list_accounts(
        UUID(user_id), account_id=account_id, limit=1
    )
    if not result['items']:
        raise HTTPException(404, 'Account not found', headers=NO_STORE)
    return result['items'][0]
