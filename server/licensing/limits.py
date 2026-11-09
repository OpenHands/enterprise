"""Checks that stop an install from going past its licensed limits.

A limit blocks only new additions. Nothing that already exists is removed or
locked. When a smaller license leaves the install over its limit, everyone
keeps working, and the install can't add more until it is back under the
limit.

The checks count rows and then add one, so two requests at the same moment
can both pass. The limits keep an install within its license in normal use;
they are not a hard guarantee.
"""

from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.licensing.service import get_license_service
from storage.database import a_session_maker
from storage.org import Org
from storage.user import ACCOUNT_TYPE_HUMAN, User


class LicenseLimitError(Exception):
    """An action would take the install past a licensed limit."""


class UserLimitReachedError(LicenseLimitError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__("You've reached your license's user limit.")


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


async def check_can_add_user(session: AsyncSession | None = None) -> None:
    """Raise ``UserLimitReachedError`` when the install can't take another user."""
    limit = user_limit()
    if limit is None:
        return
    if session is None:
        async with a_session_maker() as own_session:
            count = await count_users(own_session)
    else:
        count = await count_users(session)
    if count + 1 > limit:
        raise UserLimitReachedError(limit)


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
