"""Instance-level super-admin dashboard.

A deliberately small, self-hosted-only surface that lets a super admin:

* list every (team) organization in the instance, and
* discover whether the current user may access the dashboard at all.

Inviting users into an arbitrary org is handled by the existing
``POST /api/organizations/{org_id}/members/invite`` route: its service layer
already authorizes a super role to invite into any org (see
``OrgInvitationService`` / OHE-2769), so no separate invite endpoint lives
here.

Availability is gated at mount time by ``is_super_admin_dashboard_enabled``
(self-hosted deployments only, on by default, operator-disable via
``SUPER_ADMIN_DASHBOARD_ENABLED``). The org-listing route is additionally
gated by ``Permission.VIEW_ALL_ORGANIZATIONS``, held only by the
``superadmin`` super role. The status route is intentionally *not*
permission-gated so the frontend can cheaply decide whether to render the
dashboard nav entry without provoking a 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from server.auth.authorization import (
    Permission,
    get_user_super_role,
    has_permission,
    require_permission,
)
from storage.org_store import OrgStore

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.logger import openhands_logger as logger

admin_dashboard_router = APIRouter(prefix='/api/admin', tags=['Admin'])


class AdminOrgSummary(BaseModel):
    """Lightweight org row for the super-admin dashboard list."""

    id: str
    name: str
    contact_email: str | None = None
    is_default: bool = False


class AdminOrgListResponse(BaseModel):
    """All team organizations in the instance."""

    organizations: list[AdminOrgSummary]


class SuperAdminStatusResponse(BaseModel):
    """Whether the caller may use the super-admin dashboard."""

    is_super_admin: bool


@admin_dashboard_router.get(
    '/super-admin-status', response_model=SuperAdminStatusResponse
)
async def get_super_admin_status(
    user_id: str = Depends(get_user_id),
) -> SuperAdminStatusResponse:
    """Report whether the current user can access the super-admin dashboard.

    Returns ``is_super_admin=True`` when the user holds a super role that
    grants ``VIEW_ALL_ORGANIZATIONS``. Unlike the other dashboard routes this
    is not permission-gated: it always returns 200 for an authenticated user
    so the frontend can gate the dashboard nav entry without handling a 403.
    """
    super_role = await get_user_super_role(user_id)
    is_super_admin = super_role is not None and has_permission(
        super_role, Permission.VIEW_ALL_ORGANIZATIONS, is_super=True
    )
    return SuperAdminStatusResponse(is_super_admin=is_super_admin)


@admin_dashboard_router.get('/orgs', response_model=AdminOrgListResponse)
async def list_all_orgs(
    user_id: str = Depends(require_permission(Permission.VIEW_ALL_ORGANIZATIONS)),
) -> AdminOrgListResponse:
    """List every team organization in the instance.

    ``OrgStore.list_team_orgs`` already excludes personal workspaces (an org
    sharing its id with its owning user): they are single-user and cannot be
    invited into, so they would only add noise to the dashboard. Requires
    ``VIEW_ALL_ORGANIZATIONS``.
    """
    orgs = await OrgStore.list_team_orgs()
    summaries = [
        AdminOrgSummary(
            id=str(org.id),
            name=org.name,
            contact_email=org.contact_email,
            is_default=bool(org.is_default),
        )
        for org in orgs
    ]
    summaries.sort(key=lambda o: o.name.lower())
    logger.info(
        'admin_dashboard:list_all_orgs',
        extra={'user_id': user_id, 'org_count': len(summaries)},
    )
    return AdminOrgListResponse(organizations=summaries)
