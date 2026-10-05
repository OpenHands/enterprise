"""Instance-level Super Admin directory APIs.

Lists organizations and users across the whole deployment, and supports
suspend / resume / remove actions. Every endpoint is gated by
``Permission.MANAGE_SUPER_ADMINS`` (superadmin super role only), except
reading the instance settings, which any signed-in user can do, and the
first-install setup state, which belongs to the first Super Admin.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import false, func, select

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.auth.constants import AUTOMATION_SERVICE_URL
from server.constants import ROLE_MEMBER, ROLE_OWNER
from server.routes.org_models import (
    OrgAuthorizationError,
    OrgDatabaseError,
    OrgNotFoundError,
    OrphanedUserError,
)
from server.services.org_member_service import OrgMemberService
from server.verified_models.default_profile import DEFAULT_LLM_PROFILE_NAME
from storage.agent_profile_resolution import load_llm_profiles, member_mcp_config
from storage.database import a_session_maker
from storage.instance_settings import InstanceSettings
from storage.org import Org
from storage.org_invitation import OrgInvitation
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

MAX_LOGO_BYTES = 512 * 1024
_LOGO_DATA_URL = re.compile(r'data:image/(?:png|jpeg|webp);base64,(?P<payload>.*)')

# The automation check runs on a page load, so it gives up quickly.
_AUTOMATION_CHECK_TIMEOUT_SECONDS = 5


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


class InstanceSettingsResponse(BaseModel):
    """The company name and logo shown across the instance."""

    company_name: str | None = None
    logo: str | None = None


class InstanceSettingsUpdate(BaseModel):
    """Fields left out are unchanged; ``null`` clears a field."""

    company_name: str | None = Field(default=None, max_length=255)
    logo: str | None = None

    @field_validator('logo')
    @classmethod
    def _validate_logo(cls, value: str | None) -> str | None:
        """Accept only a PNG, JPEG, or WebP data URL of at most ``MAX_LOGO_BYTES``."""
        if value is None:
            return value
        match = _LOGO_DATA_URL.fullmatch(value)
        if match is None:
            raise ValueError('logo must be a PNG, JPEG, or WebP data URL')
        if len(base64.b64decode(match['payload'], validate=True)) > MAX_LOGO_BYTES:
            raise ValueError(f'logo must be at most {MAX_LOGO_BYTES // 1024} KB')
        return value


class SetupGuideSteps(BaseModel):
    """Setup-guide steps done in the guide's organization, read from real data."""

    org_llm: bool = False
    mcp_server: bool = False
    automation: bool = False
    invite: bool = False


class SetupStateResponse(BaseModel):
    """First-install wizard and setup-guide state for the signed-in user.

    Only the first Super Admin has a wizard and a guide; everyone else gets
    the defaults. ``guide_steps`` is set while the guide has an organization
    and is not dismissed.
    """

    wizard_pending: bool = False
    guide_org_id: str | None = None
    guide_dismissed: bool = False
    guide_steps: SetupGuideSteps | None = None


class SetupStateUpdate(BaseModel):
    """Fields left out are unchanged; a ``null`` ``guide_org_id`` clears it."""

    wizard_completed: bool = False
    guide_org_id: UUID | None = None
    guide_dismissed: bool = False


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
        ok = await OrgMemberService.remove_member_with_cleanup(org.id, user_id)
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
        await OrgMemberService.remove_member_with_cleanup(org.id, user.id)


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


def _instance_settings_response(
    settings: InstanceSettings | None,
) -> InstanceSettingsResponse:
    if settings is None:
        return InstanceSettingsResponse()
    return InstanceSettingsResponse(
        company_name=settings.company_name, logo=settings.logo
    )


@instance_admin_router.get(
    '/instance-settings',
    response_model=InstanceSettingsResponse,
)
async def get_instance_settings(
    user_id: str | None = Depends(get_user_id),
) -> InstanceSettingsResponse:
    """Return the instance company name and logo. Any signed-in user can read them."""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='User not authenticated',
        )
    async with a_session_maker() as session:
        settings = await session.get(InstanceSettings, 1)
    return _instance_settings_response(settings)


@instance_admin_router.patch(
    '/instance-settings',
    response_model=InstanceSettingsResponse,
)
async def update_instance_settings(
    body: InstanceSettingsUpdate,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> InstanceSettingsResponse:
    """Update the instance company name and logo. Requires ``MANAGE_SUPER_ADMINS``."""
    changes = body.model_dump(exclude_unset=True)
    async with a_session_maker() as session:
        settings = await session.get(InstanceSettings, 1)
        if settings is None:
            settings = InstanceSettings(id=1)
            session.add(settings)
        for field, value in changes.items():
            setattr(settings, field, value)
        await session.commit()

    logger.info(
        'admin:instance_settings:update',
        extra={'caller_user_id': caller_user_id, 'fields': sorted(changes)},
    )
    return _instance_settings_response(settings)


async def _org_has_automation(org_id: UUID, request: Request) -> bool:
    """Ask the automation service whether the organization has an automation.

    Automations live in that service's own database. The caller's own
    credentials are forwarded, so the service authorizes them as it does for
    Agent Canvas.
    """
    if not AUTOMATION_SERVICE_URL:
        return False
    headers = {
        name: value
        for name in ('cookie', 'authorization')
        if (value := request.headers.get(name))
    }
    headers['X-Org-Id'] = str(org_id)
    try:
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=_AUTOMATION_CHECK_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                f'{AUTOMATION_SERVICE_URL.rstrip("/")}/v1',
                params={'limit': 1},
                headers=headers,
            )
            response.raise_for_status()
            return response.json().get('total', 0) > 0
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            'admin:setup_state:automation_check_failed',
            extra={'org_id': str(org_id), 'error': str(exc)},
        )
        return False


async def _guide_steps(
    org_id: UUID, setup_user_id: UUID, request: Request
) -> SetupGuideSteps:
    """Derive the guide's progress from what the organization really has."""
    # Pending invitations only expire when someone touches them, so check the
    # date too. ``expires_at`` is stored as naive UTC.
    now = datetime.now(UTC).replace(tzinfo=None)
    async with a_session_maker() as session:
        org = await session.get(Org, org_id)
        member = await session.scalar(
            select(OrgMember).where(
                OrgMember.org_id == org_id, OrgMember.user_id == setup_user_id
            )
        )
        member_count = await session.scalar(
            select(func.count())
            .select_from(OrgMember)
            .where(OrgMember.org_id == org_id)
        )
        pending_invitations = await session.scalar(
            select(func.count())
            .select_from(OrgInvitation)
            .where(
                OrgInvitation.org_id == org_id,
                OrgInvitation.status == OrgInvitation.STATUS_PENDING,
                OrgInvitation.expires_at > now,
            )
        )
    # Every org gets a Default profile seeded from its default model, so only
    # a profile someone saved counts as configuring an LLM.
    org_llm = org is not None and any(
        name != DEFAULT_LLM_PROFILE_NAME for name in load_llm_profiles(org).profiles
    )
    return SetupGuideSteps(
        org_llm=org_llm,
        # MCP servers belong to each member, so this is the setup user's own.
        mcp_server=member is not None and bool(member_mcp_config(member)),
        automation=await _org_has_automation(org_id, request),
        invite=(member_count or 0) > 1 or (pending_invitations or 0) > 0,
    )


async def _setup_state_response(
    settings: InstanceSettings | None, user_id: str, request: Request
) -> SetupStateResponse:
    if settings is None or str(settings.setup_user_id) != user_id:
        return SetupStateResponse()
    guide_steps = None
    if settings.guide_org_id and not settings.guide_dismissed:
        guide_steps = await _guide_steps(settings.guide_org_id, UUID(user_id), request)
    return SetupStateResponse(
        wizard_pending=not settings.wizard_completed,
        guide_org_id=str(settings.guide_org_id) if settings.guide_org_id else None,
        guide_dismissed=settings.guide_dismissed,
        guide_steps=guide_steps,
    )


@instance_admin_router.get(
    '/setup-state',
    response_model=SetupStateResponse,
)
async def get_setup_state(
    request: Request,
    user_id: str | None = Depends(get_user_id),
) -> SetupStateResponse:
    """Return the first-install state for the caller. Any signed-in user can read it."""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='User not authenticated',
        )
    async with a_session_maker() as session:
        settings = await session.get(InstanceSettings, 1)
    return await _setup_state_response(settings, user_id, request)


@instance_admin_router.patch(
    '/setup-state',
    response_model=SetupStateResponse,
)
async def update_setup_state(
    body: SetupStateUpdate,
    request: Request,
    user_id: str | None = Depends(get_user_id),
) -> SetupStateResponse:
    """Update the first-install state. Only the first Super Admin can."""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='User not authenticated',
        )
    changes = body.model_dump(exclude_unset=True)
    async with a_session_maker() as session:
        settings = await session.get(InstanceSettings, 1)
        if settings is None or str(settings.setup_user_id) != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='Only the first Super Admin can change the setup state',
            )
        guide_org_id = changes.get('guide_org_id')
        if guide_org_id is not None and await session.get(Org, guide_org_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Organization not found',
            )
        for field, value in changes.items():
            setattr(settings, field, value)
        await session.commit()

    logger.info(
        'admin:setup_state:update',
        extra={'caller_user_id': user_id, 'fields': sorted(changes)},
    )
    return await _setup_state_response(settings, user_id, request)
