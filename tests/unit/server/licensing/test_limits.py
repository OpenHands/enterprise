from __future__ import annotations

from server.licensing.limits import count_orgs, count_users
from storage.user import ACCOUNT_TYPE_SERVICE


async def test_disabled_users_and_service_accounts_take_no_seat(
    async_session_maker, create_user
):
    create_user()
    create_user(is_disabled=True)
    create_user(deletion_pending=True)
    create_user(account_type=ACCOUNT_TYPE_SERVICE)

    async with async_session_maker() as session:
        assert await count_users(session) == 1


async def test_personal_workspaces_are_not_counted_as_orgs(
    async_session_maker, create_user, create_org
):
    personal = create_org()
    create_user(id=personal.id, current_org_id=personal.id)
    create_org()

    async with async_session_maker() as session:
        assert await count_orgs(session) == 1
