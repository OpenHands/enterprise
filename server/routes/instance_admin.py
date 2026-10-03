"""Instance-level Super Admin directory APIs.

Lists organizations and users across the whole deployment, and supports
suspend / resume / remove actions. Every endpoint is gated by
``Permission.MANAGE_SUPER_ADMINS`` (superadmin super role only).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import false, func, select

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.constants import ROLE_MEMBER, ROLE_OWNER
from server.routes.org_models import (
    OrgAuthorizationError,
    OrgDatabaseError,
    OrgNotFoundError,
    OrphanedUserError,
)
from server.services.org_member_service import OrgMemberService
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


class AdminUserGroupsUpdate(BaseModel):
    """Suspend, resume, remove, or add a user in specific organizations."""

    action: Literal['suspend', 'resume', 'remove', 'add', 'set_role']
    org_ids: list[UUID] = Field(min_length=1)
    role: Literal['owner', 'admin', 'member'] | None = None


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


def _plain_llm_api_key(settings: object) -> str:
    """Read a LiteLLM key off settings without treating mocks as secrets."""
    agent_settings = getattr(settings, 'agent_settings', None)
    llm = getattr(agent_settings, 'llm', None)
    secret = getattr(llm, 'api_key', None)
    if secret is None or secret == '':
        return ''
    if isinstance(secret, SecretStr):
        return secret.get_secret_value()
    if isinstance(secret, str):
        return secret
    return ''


async def _admin_user_response(user: User) -> AdminUserResponse:
    """Rebuild the admin user payload from current membership rows."""
    membership_pairs = await OrgMemberStore.list_memberships_with_orgs(user.id)
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
    memberships.sort(key=lambda membership: membership.org_name.lower())
    return AdminUserResponse(
        user_id=str(user.id),
        email=user.email,
        name=_display_name(user),
        memberships=memberships,
        status=_derive_user_status(memberships),
    )


def _org_status(org: Org) -> OrgStatus:
    value = getattr(org, 'status', None) or 'active'
    return 'suspended' if value == 'suspended' else 'active'


async def _is_personal_workspace(org_id: UUID) -> bool:
    """A personal workspace shares its id with the user who owns it."""
    async with a_session_maker() as session:
        result = await session.execute(select(User.id).where(User.id == org_id))
        return result.scalar_one_or_none() is not None


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
            else select(User.id).where(false())
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
    if body.status == 'suspended' and await _is_personal_workspace(org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot suspend a personal workspace',
        )

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
    if body.status == 'inactive' and str(user_id) == caller_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot suspend your own account',
        )

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

    logger.info(
        'admin:users:status',
        extra={
            'caller_user_id': caller_user_id,
            'target_user_id': str(user_id),
            'status': body.status,
        },
    )
    return await _admin_user_response(user)


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
    if str(user_id) == caller_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot remove yourself from every team organization',
        )

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
            detail=('Cannot remove user: last owner of ' + ', '.join(sorted(blocked))),
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


async def _remove_selected_memberships(user: User, org_ids: list[UUID]) -> None:
    """Remove the user from the selected team orgs, keeping the last owner."""
    selected = set(org_ids)
    if user.id in selected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot remove a user from their personal workspace',
        )
    membership_pairs = await OrgMemberStore.list_memberships_with_orgs(user.id)
    team_memberships = [
        (member, org) for member, org in membership_pairs if org.id in selected
    ]
    blocked: list[str] = []
    for member, org in team_memberships:
        role = await RoleStore.get_role_by_id(member.role_id)
        if role and role.name == ROLE_OWNER:
            if await OrgMemberService._is_last_owner(org.id, user.id):
                blocked.append(org.name)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=('Cannot remove user: last owner of ' + ', '.join(sorted(blocked))),
        )
    for _, org in team_memberships:
        await OrgMemberStore.remove_user_from_org(org.id, user.id)


async def _set_selected_roles(
    user: User,
    org_ids: list[UUID],
    role_name: str,
) -> None:
    """Change the role on existing memberships without adding new ones."""
    role = await RoleStore.get_role_by_name(role_name)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Role {role_name!r} not found',
        )
    if role_name != ROLE_OWNER:
        selected = set(org_ids)
        membership_pairs = await OrgMemberStore.list_memberships_with_orgs(user.id)
        blocked: list[str] = []
        for member, org in membership_pairs:
            if org.id not in selected:
                continue
            current = await RoleStore.get_role_by_id(member.role_id)
            if current and current.name == ROLE_OWNER:
                if await OrgMemberService._is_last_owner(org.id, user.id):
                    blocked.append(org.name)
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    'Cannot change role: last owner of ' + ', '.join(sorted(blocked))
                ),
            )
    for org_id in org_ids:
        existing = await OrgMemberStore.get_org_member(org_id, user.id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Membership not found',
            )
        await OrgMemberStore.update_user_role_in_org(org_id, user.id, role.id)


async def _add_selected_memberships(
    user: User,
    org_ids: list[UUID],
    role_name: str,
) -> None:
    """Add the user to each selected org that they are not already in."""
    role = await RoleStore.get_role_by_name(role_name)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Role {role_name!r} not found',
        )
    for org_id in org_ids:
        if await _is_personal_workspace(org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='Cannot add users to a personal workspace',
            )
        org = await OrgStore.get_org_by_id(org_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Organization not found',
            )
        existing = await OrgMemberStore.get_org_member(org_id, user.id)
        if existing is not None:
            # A suspended or invited row is not access. Granting again
            # reactivates it at the chosen role instead of leaving them out.
            if existing.status != 'active':
                await OrgMemberStore.update_user_role_in_org(
                    org_id, user.id, role.id, status='active'
                )
            continue
        settings = await OrgService.create_litellm_integration(org_id, str(user.id))
        await OrgMemberStore.add_user_to_org(
            org_id=org_id,
            user_id=user.id,
            role_id=role.id,
            llm_api_key=_plain_llm_api_key(settings),
            status='active',
        )


@instance_admin_router.post(
    '/users/{user_id}/groups',
    response_model=AdminUserResponse,
)
async def update_admin_user_groups(
    user_id: UUID,
    body: AdminUserGroupsUpdate,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> AdminUserResponse:
    """Change a user's membership in one or more organizations.

    ``suspend`` and ``resume`` update only the selected org rows.
    ``remove`` drops those memberships and still refuses to remove the
    last owner of an organization. ``add`` creates the missing memberships.
    ``set_role`` changes the role on existing memberships and refuses to
    demote the last owner.
    """
    user = await UserStore.get_user_by_id(str(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )

    if body.action in ('suspend', 'resume'):
        membership_status = 'inactive' if body.action == 'suspend' else 'active'
        try:
            await OrgMemberStore.set_all_membership_statuses(
                user_id, membership_status, body.org_ids
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
    elif body.action == 'remove':
        await _remove_selected_memberships(user, body.org_ids)
    elif body.action == 'set_role':
        if body.role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Role is required',
            )
        await _set_selected_roles(user, body.org_ids, body.role)
    else:
        await _add_selected_memberships(user, body.org_ids, body.role or ROLE_MEMBER)

    logger.info(
        'admin:users:groups',
        extra={
            'caller_user_id': caller_user_id,
            'target_user_id': str(user_id),
            'action': body.action,
            'org_ids': [str(org_id) for org_id in body.org_ids],
        },
    )
    return await _admin_user_response(user)


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
    Personal workspaces are refused: deleting one can delete its user.
    """
    if await _is_personal_workspace(org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot delete a personal workspace',
        )

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
