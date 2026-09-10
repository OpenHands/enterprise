"""Legacy account compatibility stays behind the explicit Keycloak boundary."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from server.auth import mode
from server.auth.contracts import AuthenticationUnavailable
from server.auth.keycloak.account_management import (
    AccountConflict,
    KeycloakAccountManagement,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore


@pytest.fixture
async def keycloak_accounts(async_session_maker, monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    for module in (
        'server.auth.keycloak.account_management',
        'storage.user_store',
        'storage.role_store',
    ):
        monkeypatch.setattr(module + '.a_session_maker', async_session_maker)
    monkeypatch.setattr(
        UserStore, '_acquire_user_creation_lock', AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        UserStore, '_release_user_creation_lock', AsyncMock(return_value=True)
    )
    async with async_session_maker() as session, session.begin():
        session.add_all(
            [Role(id=1, name='owner', rank=1), Role(id=2, name='admin', rank=2)]
        )
    return async_session_maker


async def canonical(factory, identifier, email='person@example.com', verified=True):
    async with factory() as session, session.begin():
        session.add(Org(id=identifier, name='user_' + str(identifier)))
        user = User(
            id=identifier,
            current_org_id=identifier,
            email=email,
            email_verified=verified,
        )
        session.add(user)
    return user


async def test_legacy_settings_hydrate_only_on_explicit_authenticated_entrypoint(
    keycloak_accounts, monkeypatch
):
    identifier = uuid4()
    async with keycloak_accounts() as session, session.begin():
        session.add(
            UserSettings(keycloak_user_id=str(identifier), already_migrated=False)
        )
    user = User(
        id=identifier,
        current_org_id=identifier,
        email='legacy@example.com',
        email_verified=True,
    )
    migrate = AsyncMock(return_value=user)
    monkeypatch.setattr(UserStore, 'migrate_user', migrate)
    assert await UserStore.get_user_by_id(str(identifier)) is None
    migrate.assert_not_awaited()
    tokens = AsyncMock()
    tokens.get_user_info_from_user_id.return_value = {
        'email': 'legacy@example.com',
        'emailVerified': True,
    }
    result = await KeycloakAccountManagement(tokens).hydrate(identifier)
    assert result.id == identifier
    assert migrate.await_args.args[0] == str(identifier)
    assert migrate.await_args.args[1].keycloak_user_id == str(identifier)
    tokens.get_user_info_from_user_id.assert_awaited_once_with(str(identifier))


async def test_legacy_email_backfill_persists_camelcase_verification(keycloak_accounts):
    identifier = uuid4()
    await canonical(keycloak_accounts, identifier, email=None, verified=None)
    tokens = AsyncMock()
    tokens.get_user_info_from_user_id.return_value = {
        'email': 'legacy@example.com',
        'emailVerified': True,
    }
    result = await KeycloakAccountManagement(tokens).hydrate(identifier)
    assert result.email == 'legacy@example.com' and result.email_verified
    async with keycloak_accounts() as session:
        saved = await session.get(User, identifier)
        assert saved.email == result.email and saved.email_verified


async def test_new_keycloak_provision_preserves_subject_personal_org_and_offline_contract(
    keycloak_accounts,
):
    identifier = uuid4()
    tokens = AsyncMock()
    tokens.get_user_id_from_user_email.return_value = None
    tokens.create_keycloak_user.return_value = str(identifier)
    tokens.request_offline_token.return_value = 'offline'
    result, created = await KeycloakAccountManagement(tokens).provision(
        'person@example.com', SecretStr('Initial password')
    )
    assert created and result.id == result.current_org_id == identifier
    tokens.create_keycloak_user.assert_awaited_once_with(
        'person@example.com', 'Initial password', email_verified=True
    )
    tokens.store_offline_token.assert_awaited_once_with(
        user_id=str(identifier), offline_token='offline'
    )
    async with keycloak_accounts() as session:
        assert await session.get(Org, identifier)
        assert (await session.get(OrgMember, (identifier, identifier))).role_id == 1


@pytest.mark.parametrize('already_canonical', [False, True])
async def test_existing_keycloak_subject_is_reconciled_without_password_reset(
    keycloak_accounts, already_canonical
):
    identifier = uuid4()
    if already_canonical:
        await canonical(keycloak_accounts, identifier)
    tokens = AsyncMock()
    tokens.get_user_id_from_user_email.return_value = str(identifier)
    result, created = await KeycloakAccountManagement(tokens).provision(
        'person@example.com', SecretStr('Must not replace password')
    )
    assert not created and result.id == identifier
    tokens.create_keycloak_user.assert_not_awaited()
    tokens.request_offline_token.assert_not_awaited()


async def test_db_only_or_changed_subject_conflict_never_reassigns_identity(
    keycloak_accounts,
):
    identifier = uuid4()
    await canonical(keycloak_accounts, identifier)
    for upstream in (None, str(uuid4())):
        tokens = AsyncMock()
        tokens.get_user_id_from_user_email.return_value = upstream
        with pytest.raises(AccountConflict):
            await KeycloakAccountManagement(tokens).provision(
                'person@example.com', SecretStr('Initial password')
            )
        tokens.create_keycloak_user.assert_not_awaited()
    async with keycloak_accounts() as session:
        assert (await session.scalar(select(User))).id == identifier


async def test_optional_offline_failure_preserves_success_and_never_resets_on_retry(
    keycloak_accounts,
):
    identifier = uuid4()
    tokens = AsyncMock()
    tokens.get_user_id_from_user_email.return_value = None
    tokens.create_keycloak_user.return_value = str(identifier)
    tokens.request_offline_token.side_effect = AuthenticationUnavailable(
        'offline unavailable'
    )
    service = KeycloakAccountManagement(tokens)
    user, created = await service.provision(
        'person@example.com', SecretStr('Initial password')
    )
    assert created and user.id == identifier
    async with keycloak_accounts() as session:
        assert await session.get(User, identifier)
    tokens.get_user_id_from_user_email.return_value = str(identifier)
    user, created = await service.provision(
        'person@example.com', SecretStr('Do not reset password')
    )
    assert not created and user.id == identifier
    assert tokens.create_keycloak_user.await_count == 1


async def test_upstream_account_creation_outage_is_not_reported_as_conflict(
    keycloak_accounts,
):
    tokens = AsyncMock()
    tokens.get_user_id_from_user_email.return_value = None
    tokens.create_keycloak_user.side_effect = AuthenticationUnavailable('unavailable')
    with pytest.raises(AuthenticationUnavailable):
        await KeycloakAccountManagement(tokens).provision(
            'new@example.com', SecretStr('Initial password')
        )
    tokens.get_user_id_from_user_email.assert_awaited_once()
