"""Instance-level super-admin management.

Exposes a small, explicit API for managing *super admins* — users whose
``user.role_id`` references the ``admin`` role row and therefore hold the
instance-level ``superadmin`` super role (see ``server.auth.authorization``
for the super-role model).

Every endpoint here is gated by the dedicated
``Permission.MANAGE_SUPER_ADMINS`` permission, which is granted **only** to
the ``superadmin`` super role — no org-scoped role can reach these routes.
In other words: only a super admin can create or remove other super admins.

Safety invariant: the API refuses to remove the **last** remaining super
admin (enforced atomically in ``UserStore.revoke_super_admin``), so an
installation can never be locked out of instance administration. A super
admin may demote themselves as long as another super admin still exists.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import (
    Permission,
    RoleName,
    get_user_super_role,
    has_permission,
    require_permission,
)
from server.constants import USER_PROVISIONING_ENABLED
from storage.user import User
from storage.user_store import SuperAdminRevokeResult, UserDeleteResult, UserStore

super_admin_router = APIRouter(prefix='/api/admin/super-admins', tags=['Admin'])


async def _list_access_guard(
    request: Request,
    user_id: str | None = Depends(get_user_id),
) -> str:
    """Auth guard for ``list_super_admins`` (OHE-3196).

    When ``USER_PROVISIONING_ENABLED`` is on (managed-users / enterprise
    mode), any authenticated user may list super-admins so non-superadmins
    can discover who their instance administrators are. When the flag is
    off, the listing stays superadmin-only via ``MANAGE_SUPER_ADMINS``
    (unchanged behavior).

    The flag is read at request time (not import time) so tests can flip
    it by patching this module's ``USER_PROVISIONING_ENABLED``.
    """
    if USER_PROVISIONING_ENABLED:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='User not authenticated',
            )
        return user_id
    # Flag off: defer to the full permission check (handles 401 + 403 and
    # the org/super-role resolution). ``require_permission`` returns a
    # closure; call it directly with the already-resolved request/user_id.
    return await require_permission(Permission.MANAGE_SUPER_ADMINS)(
        request=request, user_id=user_id
    )


class GrantSuperAdminRequest(BaseModel):
    """Identify the user to promote to super admin.

    Provide exactly one of ``user_id`` or ``email``. ``email`` is a
    convenience for installers/operators who key on email rather than the
    Keycloak ``sub``; it is resolved to a user id before the grant.
    """

    user_id: str | None = Field(
        default=None,
        description='Target user id (Keycloak sub / UUID string).',
    )
    email: str | None = Field(
        default=None,
        description='Target user email. Resolved to a user id before granting.',
    )

    @model_validator(mode='after')
    def _exactly_one_identifier(self) -> 'GrantSuperAdminRequest':
        if bool(self.user_id) == bool(self.email):
            raise ValueError('Provide exactly one of "user_id" or "email".')
        return self


class SuperAdminResponse(BaseModel):
    """A single super admin."""

    user_id: str
    email: str | None = None


class SuperAdminListResponse(BaseModel):
    """The full set of current super admins."""

    super_admins: list[SuperAdminResponse]


def _to_response(user) -> SuperAdminResponse:
    return SuperAdminResponse(user_id=str(user.id), email=user.email)


class SuperAdminStatusResponse(BaseModel):
    """Whether the authenticated caller holds the instance-level super-admin role."""

    is_super_admin: bool


@super_admin_router.get('/me', response_model=SuperAdminStatusResponse)
async def get_my_super_admin_status(
    user_id: str | None = Depends(get_user_id),
) -> SuperAdminStatusResponse:
    """Let any authenticated user learn whether *they* are a super admin.

    Unlike every other endpoint in this module, this is not gated by
    ``MANAGE_SUPER_ADMINS`` -- it only discloses the caller's own status,
    which is self-information, not an instance-admin capability. The
    frontend uses it to decide whether to show instance-wide admin
    affordances (e.g. the org-members page's "all users" view, reachable
    regardless of organization selection) without requiring the caller to
    already be a member of an organization.
    """
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='User not authenticated',
        )
    super_role = await get_user_super_role(user_id)
    is_super_admin = bool(
        super_role
        and has_permission(super_role, Permission.MANAGE_SUPER_ADMINS, is_super=True)
    )
    return SuperAdminStatusResponse(is_super_admin=is_super_admin)


@super_admin_router.get('', response_model=SuperAdminListResponse)
async def list_super_admins(
    _: str = Depends(_list_access_guard),
) -> SuperAdminListResponse:
    """List all current super admins.

    Auth is flag-gated (OHE-3196): when ``USER_PROVISIONING_ENABLED`` is on,
    any authenticated user may call this so non-superadmins can discover their
    instance administrators. Otherwise it requires ``MANAGE_SUPER_ADMINS``
    (superadmin-only), as before.
    """
    users = await UserStore.list_super_admins()
    return SuperAdminListResponse(super_admins=[_to_response(u) for u in users])


@super_admin_router.post(
    '',
    response_model=SuperAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_super_admin(
    body: GrantSuperAdminRequest,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> SuperAdminResponse:
    """Grant the super-admin role to an existing user.

    Idempotent: granting to a user who is already a super admin succeeds and
    returns their record. Requires ``MANAGE_SUPER_ADMINS``.
    """
    if body.email:
        target = await UserStore.get_user_by_email(body.email)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='No user found with that email',
            )
        target_user_id = str(target.id)
    else:
        # Validator guarantees exactly one of user_id/email is truthy, so
        # (email falsy) implies user_id is a non-empty string here. Branch on
        # truthiness -- not ``is not None`` -- to stay consistent with the
        # validator, otherwise an empty-string ``email`` would wrongly take
        # the email branch and 404.
        target_user_id = body.user_id  # type: ignore[assignment]

    try:
        user = await UserStore.grant_super_admin(target_user_id)
    except ValueError as exc:
        # Malformed user_id (not a UUID) or missing seeded admin role.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )

    logger.info(
        'super_admins:grant',
        extra={'caller_user_id': caller_user_id, 'target_user_id': target_user_id},
    )
    return _to_response(user)


@super_admin_router.delete('/{user_id}', response_model=SuperAdminResponse)
async def revoke_super_admin(
    user_id: str,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> SuperAdminResponse:
    """Revoke the super-admin role from a user (including oneself).

    Refuses with ``409 Conflict`` if the target is the only remaining super
    admin. Requires ``MANAGE_SUPER_ADMINS``.
    """
    try:
        result = await UserStore.revoke_super_admin(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if result is SuperAdminRevokeResult.NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )
    if result is SuperAdminRevokeResult.NOT_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User is not a super admin',
        )
    if result is SuperAdminRevokeResult.LAST_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot remove the last remaining super admin',
        )

    logger.info(
        'super_admins:revoke',
        extra={'caller_user_id': caller_user_id, 'target_user_id': user_id},
    )
    return SuperAdminResponse(user_id=user_id)


# ── IDP swap-over: one-time email-match seeding (ALL-5978) ────────────────


class BulkAllowMatchByEmailResponse(BaseModel):
    """Result of bulk-toggling ``allow_match_by_email``."""

    updated: int
    duplicate_emails: list[str]
    value: bool


@super_admin_router.put(
    '/allow-match-by-email',
    response_model=BulkAllowMatchByEmailResponse,
)
async def bulk_set_allow_match_by_email(
    value: bool = True,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> BulkAllowMatchByEmailResponse:
    """Bulk-set the one-time ``allow_match_by_email`` flag on all users.

    Used at the IDP swap-over moment: an operator enables the flag so that the
    next login for each user via the *new* IDP binds the new ``sub`` to the
    existing ``User`` by email. The flag self-clears on each successful link,
    so the email-match window is exactly one login wide per user.

    Requires ``MANAGE_SUPER_ADMINS`` (super-admin only).

    The response includes ``duplicate_emails`` — emails that appear on more
    than one user row. These will *not* auto-link at match time (the resolver
    returns 409 for them); the operator must resolve them manually (e.g. via
    the separate account-merge operation) before those users log in.
    """
    result = await UserStore.bulk_set_allow_match_by_email(value)
    logger.info(
        'super_admins:bulk_set_allow_match_by_email',
        extra={
            'caller_user_id': caller_user_id,
            'value': value,
            'updated': result['updated'],
            'duplicate_email_count': len(result['duplicate_emails']),
        },
    )
    return BulkAllowMatchByEmailResponse(
        updated=result['updated'],
        duplicate_emails=result['duplicate_emails'],
        value=value,
    )


# ── Instance-wide user directory ("no organization selected") ─────────────
#
# Powers the org-members page's "all users" view: when a super admin has no
# organization selected (e.g. they aren't a member of any -- see
# ``server.routes.orgs.list_user_orgs``, which only returns memberships),
# the frontend falls back to this instead of ``GET /{org_id}/members``.

admin_users_router = APIRouter(prefix='/api/admin/users', tags=['Admin'])


class AdminUserResponse(BaseModel):
    """A single instance user, for the "no organization selected" admin view."""

    user_id: str
    email: str | None = None
    is_super_admin: bool = False


class AdminUserPage(BaseModel):
    """Paginated response for ``GET /api/admin/users``."""

    items: list[AdminUserResponse]
    current_page: int = 1
    per_page: int = 10


def _to_admin_user_response(user: User) -> AdminUserResponse:
    is_super_admin = user.role is not None and user.role.name == RoleName.ADMIN.value
    return AdminUserResponse(
        user_id=str(user.id), email=user.email, is_super_admin=is_super_admin
    )


@admin_users_router.get('', response_model=AdminUserPage)
async def list_all_users(
    page_id: Annotated[
        str | None,
        Query(title='Optional offset (as a string) from a previous page'),
    ] = None,
    limit: Annotated[
        int, Query(title='The max number of results in the page', gt=0, le=100)
    ] = 10,
    email: Annotated[
        str | None,
        Query(
            title='Filter users by partial, case-insensitive email match',
            min_length=1,
            max_length=255,
        ),
    ] = None,
    _: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> AdminUserPage:
    """List every user on the instance, regardless of organization.

    Requires ``MANAGE_SUPER_ADMINS`` (super-admin only) -- this is the
    instance-wide counterpart to ``GET /{org_id}/members``.
    """
    offset = 0
    if page_id is not None:
        try:
            offset = int(page_id)
            if offset < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid page_id format',
            )

    users, _has_more = await UserStore.list_users_paginated(
        offset=offset, limit=limit, email_filter=email
    )
    current_page = (offset // limit) + 1
    return AdminUserPage(
        items=[_to_admin_user_response(u) for u in users],
        current_page=current_page,
        per_page=limit,
    )


@admin_users_router.get('/count', response_model=int)
async def count_all_users(
    email: Annotated[
        str | None,
        Query(
            title='Filter users by partial, case-insensitive email match',
            min_length=1,
            max_length=255,
        ),
    ] = None,
    _: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> int:
    """Count every user on the instance, regardless of organization."""
    return await UserStore.count_users(email_filter=email)


@admin_users_router.delete('/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> None:
    """Permanently delete a user account, in every organization it belongs to.

    This is the instance-wide "Remove" action in the "All Users" view (see
    ``UserStore.delete_user`` for exactly what gets cleaned up). Requires
    ``MANAGE_SUPER_ADMINS`` (super-admin only) -- same gate as every other
    endpoint in this file.

    Refuses with ``409 Conflict`` if the target is the only remaining super
    admin, for the same reason ``revoke_super_admin`` does: deleting that
    row would remove the super-admin role from the instance entirely.
    """
    result = await UserStore.delete_user(user_id)

    if result is UserDeleteResult.NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
        )
    if result is UserDeleteResult.LAST_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot remove the last remaining super admin',
        )

    logger.info(
        'admin_users:delete',
        extra={'caller_user_id': caller_user_id, 'target_user_id': user_id},
    )
