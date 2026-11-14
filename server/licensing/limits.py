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

from fastapi import HTTPException, status
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.licensing.service import (
    UNLICENSED_CONVERSATION_LIMIT,
    get_license_service,
)
from storage.database import a_session_maker
from storage.instance_settings import InstanceSettings
from storage.org import Org
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.user import ACCOUNT_TYPE_HUMAN, User

# The ``error`` in the 402 that refuses a conversation or a sandbox. The
# automation service reads it to mark the run skipped.
LICENSE_LIMIT_REACHED = 'LICENSE_LIMIT_REACHED'


class LicenseLimitError(Exception):
    """An action would take the install past a licensed limit."""


class UserLimitReachedError(LicenseLimitError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__("You've reached your license's user limit.")


class OrgLimitReachedError(LicenseLimitError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__("You've reached your license's organization limit.")


class ConversationLimitReachedError(LicenseLimitError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(
            f'Without a valid license, this install can start {limit} '
            'conversations, and it has used them all.'
        )


class FirstSuperAdminOnlyError(LicenseLimitError):
    def __init__(self) -> None:
        super().__init__(
            'Without a valid license, only the first Super Admin can start '
            'conversations.'
        )


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


async def check_can_add_org() -> None:
    """Raise ``OrgLimitReachedError`` when the install can't take another org."""
    limit = org_limit()
    if limit is None:
        return
    async with a_session_maker() as session:
        count = await count_orgs(session)
    if count + 1 > limit:
        raise OrgLimitReachedError(limit)


async def count_conversations(session: AsyncSession) -> int:
    """The number of conversations the install has ever started.

    A deleted conversation keeps its row, so it still counts.
    """
    count = await session.scalar(
        select(func.count()).select_from(StoredConversationMetadata)
    )
    return count or 0


async def check_can_start_conversation(user_id: str | None) -> None:
    """Raise ``LicenseLimitError`` when ``user_id`` can't start a conversation.

    Only an install without a valid license is held to this. It can start
    ``UNLICENSED_CONVERSATION_LIMIT`` conversations in its lifetime, and only
    its first Super Admin can start them.
    """
    if get_license_service().is_licensed():
        return
    async with a_session_maker() as session:
        settings = await session.get(InstanceSettings, 1)
        count = await count_conversations(session)
    # The limit comes first: once it is reached, nobody can start one.
    if count + 1 > UNLICENSED_CONVERSATION_LIMIT:
        raise ConversationLimitReachedError(UNLICENSED_CONVERSATION_LIMIT)
    first_super_admin = settings.setup_user_id if settings else None
    if first_super_admin is not None:
        allowed = str(first_super_admin) == user_id
    else:
        # No first Super Admin is on record when the install had users before
        # it recorded one, or when that user was deleted. Any Super Admin can
        # start conversations then. Imported here because authorization
        # imports the user store, which imports this module.
        from server.auth.authorization import is_instance_super_admin

        allowed = user_id is not None and await is_instance_super_admin(user_id)
    if not allowed:
        raise FirstSuperAdminOnlyError()


async def require_can_start_conversation(user_id: str | None) -> None:
    """Reject the request with 402 when ``user_id`` can't start a conversation.

    The answer has an ``error`` code, and is not the 403 the other limits
    answer with: the automation service calls the routes that use this, and
    it takes a 403 for an expired API key and tells limits apart by code.
    """
    try:
        await check_can_start_conversation(user_id)
    except LicenseLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={'error': LICENSE_LIMIT_REACHED, 'message': str(e)},
        ) from e
