"""What the license limits count, and the error for going past a limit."""

from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.licensing.service import get_license_service
from storage.org import Org
from storage.user import ACCOUNT_TYPE_HUMAN, User


class LicenseLimitError(Exception):
    """An action would take the install past a licensed limit."""


async def count_users(session: AsyncSession) -> int:
    """The number of users that take a seat.

    A seat is a person whose account is not disabled or being deleted.
    Service accounts don't take one.
    """
    count = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.account_type == ACCOUNT_TYPE_HUMAN,
            User.is_disabled.is_(False),
            User.deletion_pending.is_(False),
        )
    )
    return count or 0


def user_limit() -> int | None:
    """The licensed number of users, or None when there is no limit."""
    limits = get_license_service().limits()
    return None if limits is None else limits.users


async def count_orgs(session: AsyncSession) -> int:
    """The number of team organizations.

    A personal workspace shares its id with its user, and doesn't count.
    """
    count = await session.scalar(
        select(func.count()).select_from(Org).where(~exists().where(User.id == Org.id))
    )
    return count or 0


def org_limit() -> int | None:
    """The licensed number of team organizations, or None when there is no limit."""
    limits = get_license_service().limits()
    return None if limits is None else limits.orgs
