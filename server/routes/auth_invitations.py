"""Administrator-issued enrollment links and organization choices."""

from typing import TypedDict
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from server.auth.authorization import Permission, require_permission
from server.auth.native_types import (
    InvitationLink,
    InvitationOrganizationPage,
    InvitationPage,
)
from server.routes.native_auth import NativeAuthRoute
from server.services.native_enrollment_service import get_native_enrollment_service

MANAGE_USERS = require_permission(Permission.MANAGE_USERS)
auth_invitations_router = APIRouter(
    prefix='/api/admin', tags=['Admin'], route_class=NativeAuthRoute
)


class AuthRole(TypedDict):
    id: int
    name: str


class InvitationBody(BaseModel):
    email: str = Field(max_length=320)
    org_id: UUID | None = None
    org_role_id: int | None = None


@auth_invitations_router.get('/auth-organizations')
async def list_invitation_organizations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user_id: str = Depends(MANAGE_USERS),
) -> InvitationOrganizationPage:
    return await get_native_enrollment_service().list_invitation_organizations(
        UUID(user_id), offset=offset, limit=limit
    )


@auth_invitations_router.get('/auth-roles')
async def list_roles(user_id: str = Depends(MANAGE_USERS)) -> list[AuthRole]:
    from sqlalchemy import select

    from server.services.native_account_service import require_active_admin
    from storage.database import a_session_maker
    from storage.role import Role

    async with a_session_maker() as session:
        await require_active_admin(session, UUID(user_id))
        roles = await session.scalars(
            select(Role)
            .where(Role.name.in_(('owner', 'admin', 'member')))
            .order_by(Role.id)
        )
        return [{'id': role.id, 'name': role.name} for role in roles]


@auth_invitations_router.post('/auth-invitations', status_code=201)
async def issue_invitation(
    body: InvitationBody, user_id: str = Depends(MANAGE_USERS)
) -> InvitationLink:
    return await get_native_enrollment_service().issue_invitation(
        UUID(user_id), body.email, body.org_id, body.org_role_id
    )


@auth_invitations_router.get('/auth-invitations')
async def list_invitations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str = Depends(MANAGE_USERS),
) -> InvitationPage:
    return await get_native_enrollment_service().list_invitations(
        UUID(user_id), offset=offset, limit=limit
    )


@auth_invitations_router.delete('/auth-invitations/{invitation_id}', status_code=204)
async def revoke_invitation(
    invitation_id: UUID, user_id: str = Depends(MANAGE_USERS)
) -> None:
    await get_native_enrollment_service().revoke_invitation(
        UUID(user_id), invitation_id
    )


@auth_invitations_router.post('/auth-invitations/{invitation_id}/reissue')
async def reissue_invitation(
    invitation_id: UUID, user_id: str = Depends(MANAGE_USERS)
) -> InvitationLink:
    return await get_native_enrollment_service().reissue_invitation(
        UUID(user_id), invitation_id
    )
