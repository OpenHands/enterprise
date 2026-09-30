"""Org lifecycle gates for product usage.

Suspension is enforced at the org-resolution choke points so anything that
runs in an org context (conversations/agents, API keys, resolver
integrations) is blocked without sprinkling checks on every route.

Admin directory APIs (``/api/admin/*``) do **not** go through
``get_effective_org_id`` and remain available to super admins.
"""

from __future__ import annotations

from uuid import UUID

from openhands.app_server.utils.logger import openhands_logger as logger


class OrgNotUsableError(Exception):
    """Raised when an org/membership must not be used for product work."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


async def assert_org_usable_for_product(
    org_id: UUID,
    *,
    user_id: str | None = None,
    allow_instance_super_admin: bool = True,
) -> None:
    """Raise ``OrgNotUsableError`` when ``org_id`` must not be used for product work.

    Blocks:

    * Organizations with ``status == "suspended"``
    * Users whose membership in that org has ``status == "inactive"``

    Instance Super Admins (``manage_super_admins``) bypass these gates by
    default so they can open and administer suspended orgs. Pass
    ``allow_instance_super_admin=False`` for paths that must stay blocked
    even for them (resolver/webhook conversation starts).

    Call this whenever an org id is about to become the request's effective
    org (``SaasUserAuth.get_effective_org_id``) or a resolver conversation
    target (``resolve_org_for_repo`` / override org).
    """
    # Local imports keep this module free of circular imports with stores
    # that pull authorization helpers.
    from storage.org_member_store import OrgMemberStore
    from storage.org_store import OrgStore

    if allow_instance_super_admin and user_id:
        from server.auth.authorization import is_instance_super_admin

        if await is_instance_super_admin(user_id):
            return

    org = await OrgStore.get_org_by_id(org_id)
    if org is None:
        # Callers that need existence checks still own 404 semantics; this
        # helper only enforces lifecycle when the org row is present.
        return

    if getattr(org, 'status', 'active') == 'suspended':
        logger.info(
            'org_access:org_suspended',
            extra={
                'org_id': str(org_id),
                'user_id': user_id,
            },
        )
        raise OrgNotUsableError('Organization is suspended')

    if user_id is None:
        return

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return

    member = await OrgMemberStore.get_org_member(org_id, user_uuid)
    if member is not None and member.status == 'inactive':
        logger.info(
            'org_access:membership_inactive',
            extra={
                'org_id': str(org_id),
                'user_id': user_id,
            },
        )
        raise OrgNotUsableError('User membership is suspended')
