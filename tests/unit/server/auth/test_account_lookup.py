"""Selected account lookups preserve migration and durable-account boundaries."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from integrations import stripe_service
from server.auth import account_lookup
from server.auth.account_lookup import KeycloakAccountLookup, OpenHandsAccountLookup
from server.auth.native_types import SessionFactory
from server.auth.token_manager import TokenManager
from server.services import account_profile_provisioning
from storage import role_store, user_store
from storage.lite_llm_manager import LiteLlmManager
from storage.native_auth import AuthAccount
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore


@pytest.fixture
async def lookup_database(
    async_session_maker: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SessionFactory]:
    for module in (
        account_lookup,
        account_profile_provisioning,
        user_store,
        role_store,
    ):
        monkeypatch.setattr(module, 'a_session_maker', async_session_maker)
    yield async_session_maker


@pytest.mark.parametrize('migration_fails', [False, True])
async def test_keycloak_lookup_migrates_legacy_profile_and_releases_lock(
    lookup_database: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    migration_fails: bool,
) -> None:
    account_id = uuid4()
    user_id = str(account_id)
    async with lookup_database() as session, session.begin():
        session.add(Role(name='owner', rank=0))
        session.add(
            UserSettings(
                keycloak_user_id=user_id,
                email='legacy@example.test',
                user_version=1,
                agent_settings={
                    'llm': {
                        'model': 'custom/model',
                        'base_url': 'https://llm.example.test',
                    }
                },
                conversation_settings={},
                already_migrated=False,
            )
        )
    acquire = AsyncMock(return_value=True)
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(UserStore, '_acquire_user_creation_lock', acquire)
    monkeypatch.setattr(UserStore, '_release_user_creation_lock', release)
    monkeypatch.setattr(
        TokenManager,
        'get_user_info_from_user_id',
        AsyncMock(return_value={'id': user_id, 'email': 'legacy@example.test'}),
    )
    monkeypatch.setattr(stripe_service, 'migrate_customer', AsyncMock())
    monkeypatch.setattr(
        LiteLlmManager,
        'migrate_entries',
        AsyncMock(side_effect=RuntimeError('Unavailable') if migration_fails else None),
    )

    assert await UserStore.get_user_by_id(user_id) is None
    acquire.assert_not_awaited()
    lookup = KeycloakAccountLookup()
    if migration_fails:
        with pytest.raises(RuntimeError, match='Unavailable'):
            await lookup.get_user_by_id(user_id)
    else:
        user = await lookup.get_user_by_id(user_id)
        assert user is not None
        assert user.id == account_id == user.current_org_id
        assert len(user.org_members) == 1
        assert await lookup.get_user_by_id(user_id) is not None
    acquire.assert_awaited_once_with(user_id)
    release.assert_awaited_once_with(user_id)
    async with lookup_database() as session:
        legacy = await session.scalar(
            select(UserSettings).where(UserSettings.keycloak_user_id == user_id)
        )
        assert legacy is not None
        assert legacy.already_migrated is not migration_fails
        assert (await session.get(Org, account_id) is None) is migration_fails
        assert (
            await session.get(OrgMember, (account_id, account_id)) is None
        ) is migration_fails


@pytest.mark.parametrize(
    'account_state',
    [None, 'deleted', 'reonboardable', 'profile_absent_blocked', 'profile_present'],
)
@pytest.mark.parametrize('disabled', [False, True])
async def test_openhands_profile_lookup_requires_durable_profile_state(
    lookup_database: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    account_state: str | None,
    disabled: bool,
) -> None:
    account_id = uuid4()
    email = 'mixed@example.test'
    async with lookup_database() as session, session.begin():
        session.add(Org(id=account_id, name='Personal'))
        session.add(
            User(
                id=account_id,
                current_org_id=account_id,
                email=email,
                is_disabled=disabled,
            )
        )
        if account_state is not None:
            session.add(
                AuthAccount(
                    id=account_id,
                    normalized_email=email,
                    display_email=email,
                    state=account_state,
                )
            )
        session.add(
            UserSettings(keycloak_user_id=str(account_id), already_migrated=False)
        )
    migrate = AsyncMock(
        side_effect=AssertionError('Native lookup must never migrate a legacy identity')
    )
    monkeypatch.setattr(
        account_profile_provisioning.KeycloakAccountProfileProvisioning,
        'migrate_user',
        migrate,
    )
    lookup = OpenHandsAccountLookup()
    expected = account_state == 'profile_present'
    assert (await lookup.get_user_by_id(str(account_id)) is not None) is expected
    assert (
        await lookup.get_user_by_email(' MIXED@EXAMPLE.TEST ') is not None
    ) is expected
    async with lookup_database() as session, session.begin():
        assert (
            await lookup.get_profile_for_update(session, str(account_id)) is not None
        ) is (expected and not disabled)
    migrate.assert_not_awaited()
