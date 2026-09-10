"""Noncritical login analytics, scheduled after account admission completes."""

import asyncio
from typing import Any
from uuid import UUID

from openhands.analytics import get_analytics_service
from server.logger import logger
from storage.user import User

_pending: set[asyncio.Task[Any]] = set()


def schedule_login_analytics(user: User, idp: str | None = None) -> None:
    task = asyncio.create_task(
        _track_login_analytics_background(
            user_id=str(user.id),
            email=user.email,
            idp=idp,
            current_org_id=user.current_org_id,
            org_member_ids=[member.org_id for member in user.org_members],
            consented=user.user_consents_to_analytics is True,
        )
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _get_user_orgs_with_data(user_id: str, org_member_ids: list) -> list:
    """Load Org objects for a user's org memberships.

    Uses OrgStore.get_orgs_by_ids() to batch-load all Org objects in a single
    query, avoiding N+1.

    Args:
        user_id: The user's ID string
        org_member_ids: List of org_id UUIDs from user.org_members

    Returns:
        List of Org objects the user belongs to
    """
    from storage.org_store import OrgStore

    if not org_member_ids:
        return []

    try:
        return await OrgStore.get_orgs_by_ids(org_member_ids)
    except Exception:
        logger.exception(
            'auth:_get_user_orgs_with_data:failed',
            extra={'user_id': user_id, 'org_ids': [str(oid) for oid in org_member_ids]},
            stack_info=True,
        )
        return []


async def _track_login_analytics_background(
    user_id: str,
    email: str | None,
    idp: str | None,
    current_org_id: UUID | None,
    org_member_ids: list,
    consented: bool,
) -> None:
    """Track login analytics in background to avoid blocking auth response."""
    try:
        from storage.org_member_store import OrgMemberStore
        from storage.org_store import OrgStore

        analytics = get_analytics_service()
        if not analytics:
            return

        org_id_str = str(current_org_id) if current_org_id else None

        # Load current org
        current_org = (
            await OrgStore.get_org_by_id(current_org_id) if current_org_id else None
        )

        # Load org data (orgs list with member_count)
        user_orgs = await _get_user_orgs_with_data(user_id, org_member_ids)

        orgs_data = []
        for org in user_orgs:
            try:
                member_count = await OrgMemberStore.get_org_members_count(org_id=org.id)
            except Exception:
                logger.exception(
                    'auth:identify_user:member_count_failed',
                    extra={'user_id': user_id, 'org_id': str(org.id)},
                    stack_info=True,
                )
                member_count = None
            orgs_data.append(
                {'id': str(org.id), 'name': org.name, 'member_count': member_count}
            )

        from openhands.analytics.analytics_context import AnalyticsContext

        ctx = AnalyticsContext(
            user_id=user_id,
            consented=consented,
            org_id=org_id_str,
            user=None,
        )

        analytics.identify_user(
            ctx=ctx,
            email=email,
            org_name=current_org.name if current_org else None,
            idp=idp,
            orgs=orgs_data,
        )

        analytics.track_user_logged_in(
            ctx=ctx,
            idp=idp,
        )
    except Exception:
        logger.exception(
            'auth:_track_login_analytics_background:failed',
            extra={'user_id': user_id},
            stack_info=True,
        )
