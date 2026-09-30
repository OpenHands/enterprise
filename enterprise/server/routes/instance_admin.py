"""Instance-level Super Admin directory APIs.

Lists organizations and users across the whole deployment, and supports
suspend / resume / remove actions. Every endpoint is gated by
``Permission.MANAGE_SUPER_ADMINS`` (superadmin super role only).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from server.auth.authorization import Permission, require_permission
from server.constants import ROLE_OWNER
from server.routes.org_models import (
    OrgAuthorizationError,
    OrgDatabaseError,
    OrgNotFoundError,
    OrphanedUserError,
)
from server.services.org_member_service import OrgMemberService
from sqlalchemy import func, select
from storage.database import a_session_maker
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.org_service import OrgService
from storage.org_store import OrgStore
from storage.role import Role
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import UserStore

from openhands.app_server.utils.logger import openhands_logger as logger

instance_admin_router = APIRouter(prefix='/api/admin', tags=['Admin'])

OrgStatus = Literal['active', 'suspended']
UserMembershipStatus = Literal['active', 'inactive']


class AdminOrgResponse(BaseModel):
    """A single organization in the instance directory."""

    id: str
    name: str
    contact_email: str | None = None
    contact_name: str | None = None
    member_count: int = 0
    is_personal: bool = False
    status: OrgStatus = 'active'


class AdminOrgListResponse(BaseModel):
    """All organizations on the instance."""

    organizations: list[AdminOrgResponse]


class AdminOrgStatusUpdate(BaseModel):
    """Suspend or resume an organization."""

    status: OrgStatus


class AdminMembershipResponse(BaseModel):
    """One org membership for a user in the instance directory."""

    org_id: str
    org_name: str
    role: str
    status: str | None = None


class AdminUserResponse(BaseModel):
    """A single user in the instance directory."""

    user_id: str
    email: str | None = None
    name: str | None = None
    memberships: list[AdminMembershipResponse] = Field(default_factory=list)
    status: UserMembershipStatus = 'active'


class AdminUserListResponse(BaseModel):
    """All users on the instance."""

    users: list[AdminUserResponse]


class AdminUserStatusUpdate(BaseModel):
    """Suspend or resume a user across all memberships."""

    status: UserMembershipStatus


def _display_name(user: User) -> str | None:
    if user.git_user_name and user.git_user_name.strip():
        return user.git_user_name.strip()
    if user.email and '@' in user.email:
        return user.email.split('@', 1)[0]
    return user.email


def _derive_user_status(
    memberships: list[AdminMembershipResponse],
) -> UserMembershipStatus:
    if memberships and all(m.status == 'inactive' for m in memberships):
        return 'inactive'
    return 'active'


def _org_status(org: Org) -> OrgStatus:
    value = getattr(org, 'status', None) or 'active'
    return 'suspended' if value == 'suspended' else 'active'


@instance_admin_router.get(
    '/organizations',
    response_model=AdminOrgListResponse,
)
async def list_admin_organizations(
    _: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> AdminOrgListResponse:
    """List every organization with member counts. Requires ``MANAGE_SUPER_ADMINS``."""
    async with a_session_maker() as session:
        count_subq = (
            select(
                OrgMember.org_id.label('org_id'),
                func.count().label('member_count'),
            )
            .group_by(OrgMember.org_id)
            .subquery()
        )
        result = await session.execute(
            select(Org, func.coalesce(count_subq.c.member_count, 0))
            .outerjoin(count_subq, Org.id == count_subq.c.org_id)
            .order_by(Org.name)
        )
        rows = result.all()

    organizations = [
        AdminOrgResponse(
            id=str(org.id),
            name=org.name,
            contact_email=org.contact_email,
            contact_name=org.contact_name,
            member_count=int(member_count),
            is_personal=False,
            status=_org_status(org),
        )
        for org, member_count in rows
    ]
    # Mark personal workspaces (org.id == a user.id) when we can cheaply detect them.
    user_ids = {org.id for org, _ in rows}
    async with a_session_maker() as session:
        user_result = await session.execute(
            select(User.id).where(User.id.in_(user_ids))
            if user_ids
            else select(User.id).where(False)
        )
        personal_ids = {str(uid) for uid in user_result.scalars().all()}

    for org in organizations:
        org.is_personal = org.id in personal_ids

    return AdminOrgListResponse(organizations=organizations)


@instance_admin_router.patch(
    '/organizations/{org_id}',
    response_model=AdminOrgResponse,
)
async def update_admin_organization_status(
    org_id: UUID,
    body: AdminOrgStatusUpdate,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> AdminOrgResponse:
    """Suspend or resume an organization. Requires ``MANAGE_SUPER_ADMINS``."""
    try:
        org = await OrgStore.set_org_status(org_id, body.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Organization not found'
        )

    member_count = await OrgMemberStore.get_org_members_count(org_id)
    logger.info(
        'admin:organizations:status',
        extra={
            'caller_user_id': caller_user_id,
            'org_id': str(org_id),
            'status': body.status,
        },
    )
    return AdminOrgResponse(
        id=str(org.id),
        name=org.name,
        contact_email=org.contact_email,
        contact_name=org.contact_name,
        member_count=member_count,
        is_personal=False,
        status=_org_status(org),
    )


@instance_admin_router.get(
    '/users',
    response_model=AdminUserListResponse,
)
async def list_admin_users(
    _: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> AdminUserListResponse:
    """List every user with org memberships. Requires ``MANAGE_SUPER_ADMINS``."""
    async with a_session_maker() as session:
        users = list(
            (await session.execute(select(User).order_by(User.email))).scalars()
        )
        membership_rows = (
            await session.execute(
                select(OrgMember, Org, Role)
                .join(Org, Org.id == OrgMember.org_id)
                .join(Role, Role.id == OrgMember.role_id)
            )
        ).all()

    by_user: dict[UUID, list[AdminMembershipResponse]] = {}
    for org_member, org, role in membership_rows:
        by_user.setdefault(org_member.user_id, []).append(
            AdminMembershipResponse(
                org_id=str(org.id),
                org_name=org.name,
                role=role.name,
                status=org_member.status,
            )
        )

    users_out = []
    for user in users:
        memberships = sorted(
            by_user.get(user.id, []),
            key=lambda m: m.org_name.lower(),
        )
        users_out.append(
            AdminUserResponse(
                user_id=str(user.id),
                email=user.email,
                name=_display_name(user),
                memberships=memberships,
                status=_derive_user_status(memberships),
            )
        )
    return AdminUserListResponse(users=users_out)


@instance_admin_router.patch(
    '/users/{user_id}',
    response_model=AdminUserResponse,
)
async def update_admin_user_status(
    user_id: UUID,
    body: AdminUserStatusUpdate,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> AdminUserResponse:
    """Suspend or resume a user across all org memberships.

    Sets every ``org_member.status`` for the user to ``inactive`` (suspend)
    or ``active`` (resume). Requires ``MANAGE_SUPER_ADMINS``.
    """
    user = await UserStore.get_user_by_id(str(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )

    try:
        await OrgMemberStore.set_all_membership_statuses(user_id, body.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    membership_pairs = await OrgMemberStore.list_memberships_with_orgs(user_id)
    memberships: list[AdminMembershipResponse] = []
    for org_member, org in membership_pairs:
        role = await RoleStore.get_role_by_id(org_member.role_id)
        memberships.append(
            AdminMembershipResponse(
                org_id=str(org.id),
                org_name=org.name,
                role=role.name if role else 'member',
                status=org_member.status,
            )
        )
    memberships.sort(key=lambda m: m.org_name.lower())

    logger.info(
        'admin:users:status',
        extra={
            'caller_user_id': caller_user_id,
            'target_user_id': str(user_id),
            'status': body.status,
        },
    )
    return AdminUserResponse(
        user_id=str(user.id),
        email=user.email,
        name=_display_name(user),
        memberships=memberships,
        status=_derive_user_status(memberships),
    )


@instance_admin_router.delete(
    '/users/{user_id}',
    status_code=status.HTTP_200_OK,
)
async def remove_admin_user_from_orgs(
    user_id: UUID,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> dict:
    """Remove a user from every team organization.

    Keeps the personal workspace (``org.id == user.id``) so the account can
    still sign in. Refuses with ``409`` if the user is the last owner of any
    team org. Requires ``MANAGE_SUPER_ADMINS``.
    """
    user = await UserStore.get_user_by_id(str(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )

    membership_pairs = await OrgMemberStore.list_memberships_with_orgs(user_id)
    team_memberships = [
        (member, org) for member, org in membership_pairs if org.id != user_id
    ]

    blocked: list[str] = []
    for member, org in team_memberships:
        role = await RoleStore.get_role_by_id(member.role_id)
        if role and role.name == ROLE_OWNER:
            if await OrgMemberService._is_last_owner(org.id, user_id):
                blocked.append(org.name)

    if blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                'Cannot remove user: last owner of '
                + ', '.join(sorted(blocked))
            ),
        )

    removed: list[str] = []
    for _, org in team_memberships:
        ok = await OrgMemberStore.remove_user_from_org(org.id, user_id)
        if ok:
            removed.append(str(org.id))

    logger.info(
        'admin:users:remove',
        extra={
            'caller_user_id': caller_user_id,
            'target_user_id': str(user_id),
            'removed_org_ids': removed,
        },
    )
    return {
        'message': 'User removed from team organizations',
        'user_id': str(user_id),
        'removed_org_ids': removed,
    }


@instance_admin_router.delete(
    '/organizations/{org_id}',
    status_code=status.HTTP_200_OK,
)
async def delete_admin_organization(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> dict:
    """Delete any organization as a super admin.

    Uses the same cleanup path as owner deletion, but authorization is the
    instance-level ``MANAGE_SUPER_ADMINS`` permission rather than org ownership.
    """
    logger.info(
        'admin:organizations:delete',
        extra={'caller_user_id': user_id, 'org_id': str(org_id)},
    )
    try:
        deleted_org = await OrgService.delete_org_with_cleanup(
            user_id=user_id,
            org_id=org_id,
            allow_super_admin=True,
        )
    except OrgNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OrgAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except OrphanedUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except OrgDatabaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return {
        'message': 'Organization deleted successfully',
        'organization': {
            'id': str(deleted_org.id),
            'name': deleted_org.name,
            'contact_name': deleted_org.contact_name,
            'contact_email': deleted_org.contact_email,
        },
    }
