"""Account policy and identity storage behavior against SQLite."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from server.auth import mode
from server.auth.contracts import Principal
from server.auth.local.accounts import create_local_account
from server.auth.local.credentials import LocalPasswordCredentialService
from server.auth.user_management import (
    AccountConflict,
    AccountPermissionError,
    EnterpriseUserManagementService,
)
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
)
from server.services.org_invitation_service import OrgInvitationService
from storage.api_key import ApiKey
from storage.auth_sessions import AuthSession
from storage.local_credentials import LocalCredentials
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_offline_token import StoredOfflineToken
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import SuperAdminRevokeResult, UserStore

PASSWORD = SecretStr('A long original account password')


@pytest.fixture
async def accounts_db(async_session_maker, monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    for module in (
        'storage.user_store',
        'storage.org_store',
        'storage.role_store',
        'storage.org_member_store',
        'storage.org_invitation_store',
        'server.services.org_invitation_service',
        'server.services.admin_user_lifecycle_service',
        'server.auth.user_management',
    ):
        monkeypatch.setattr(module + '.a_session_maker', async_session_maker)
    async with async_session_maker() as session, session.begin():
        session.add_all(
            [
                Role(id=1, name='owner', rank=1),
                Role(id=2, name='admin', rank=2),
                Role(id=3, name='member', rank=3),
            ]
        )
    yield async_session_maker


async def account(factory, email='person@example.com', **kwargs):
    async with factory() as session, session.begin():
        return await create_local_account(session, email, PASSWORD, **kwargs)


def actor(user):
    return Principal(user.id, 'password', datetime.now(UTC))


async def test_creation_uses_enterprise_permission_and_never_grants_first_user_admin(
    accounts_db,
):
    ordinary = await account(accounts_db)
    service = EnterpriseUserManagementService(accounts_db)
    with pytest.raises(AccountPermissionError):
        await service.create('other@example.com', PASSWORD, actor=actor(ordinary))
    admin = await account(accounts_db, 'admin@example.com', instance_admin=True)
    created = await service.create('NEW+Tag@example.com', PASSWORD, actor=actor(admin))
    assert created.role_id is None
    assert created.email == 'new+tag@example.com'
    assert not created.email_verified
    async with accounts_db() as session:
        assert (await session.get(User, created.id)).current_org_id == created.id
        assert (await session.get(LocalCredentials, created.id)).must_change_password
        assert (await session.get(OrgMember, (created.id, created.id))).role_id == 1
    with pytest.raises(AccountConflict):
        await service.create('new+tag@example.com', PASSWORD, actor=actor(admin))


async def test_provision_retries_preserve_password_membership_role_and_org_key(
    accounts_db, monkeypatch
):
    admin = await account(accounts_db, 'admin@example.com', instance_admin=True)
    team = Org(id=uuid4(), name='team')
    second_team = Org(id=uuid4(), name='second-team')
    async with accounts_db() as session, session.begin():
        session.add_all([team, second_team])
    service = EnterpriseUserManagementService(accounts_db)
    monkeypatch.setattr(service, 'ensure_llm_provisioned', AsyncMock())
    user, created = await service.provision_account(
        'new@example.com', PASSWORD, actor=actor(admin), organization_id=team.id
    )
    assert created
    assert await service.provision_membership(
        user.id, team.id, 'admin', actor=actor(admin)
    )
    key = await service.provision_api_key(
        user.id, team.id, 'Initial API Key', actor=actor(admin)
    )
    retried, created = await service.provision_account(
        'NEW@example.com',
        SecretStr('Changed password must not apply'),
        actor=actor(admin),
        organization_id=team.id,
    )
    assert not created and retried.id == user.id
    assert not await service.provision_membership(
        user.id, team.id, 'member', actor=actor(admin)
    )
    assert (
        await service.provision_api_key(
            user.id, team.id, 'Initial API Key', actor=actor(admin)
        )
        == key
    )
    assert await service.provision_membership(
        user.id, second_team.id, 'member', actor=actor(admin)
    )
    second_key = await service.provision_api_key(
        user.id, second_team.id, 'Initial API Key', actor=actor(admin)
    )
    assert second_key != key
    replacement = await service.provision_api_key(
        user.id, second_team.id, 'Initial API Key', actor=actor(admin), reissue=True
    )
    assert replacement not in (key, second_key)
    assert (
        await LocalPasswordCredentialService(accounts_db).verify(user.email, PASSWORD)
    ).id == user.id
    async with accounts_db() as session:
        assert (await session.get(OrgMember, (team.id, user.id))).role_id == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ApiKey)
                .where(ApiKey.user_id == str(user.id))
            )
            == 2
        )


async def test_database_reads_and_local_missing_hydration_never_construct_keycloak(
    accounts_db, monkeypatch
):
    from server.auth.keycloak.account_management import KeycloakAccountManagement

    monkeypatch.setattr(
        KeycloakAccountManagement,
        '__init__',
        lambda *args, **kwargs: pytest.fail('Keycloak constructed in local mode'),
    )
    identifier = uuid4()
    async with accounts_db() as session, session.begin():
        session.add(
            UserSettings(keycloak_user_id=str(identifier), already_migrated=False)
        )
    assert await UserStore.get_user_by_id(str(identifier)) is None
    assert (
        await EnterpriseUserManagementService(accounts_db).ensure_authenticated_account(
            identifier
        )
        is None
    )


async def test_disabled_user_revokes_sessions_api_keys_and_offline_tokens_before_remote(
    accounts_db, monkeypatch
):
    user = await account(accounts_db)
    issued = await LocalPasswordCredentialService(accounts_db).login(
        user.email, PASSWORD
    )
    async with accounts_db() as session, session.begin():
        session.add(ApiKey(user_id=str(user.id), key='old-api-key', org_id=user.id))
        session.add(
            StoredOfflineToken(user_id=str(user.id), offline_token='opaque-old-token')
        )
    service = AdminUserLifecycleService(session_factory=accounts_db)
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)

    async def remote_failure(*args):
        async with accounts_db() as session:
            assert (await session.get(User, user.id)).is_disabled
            assert await session.get(AuthSession, issued.principal.session_id) is None
            assert (
                await session.scalar(
                    select(ApiKey).where(ApiKey.user_id == str(user.id))
                )
                is None
            )
            assert await session.get(StoredOfflineToken, str(user.id)) is None
        raise RuntimeError('secret-bearing provider response must not leak')

    adapter = AsyncMock()
    adapter.set_enabled.side_effect = remote_failure
    monkeypatch.setattr(service, '_keycloak', lambda: adapter)
    result = await service.disable_user(str(user.id))
    assert result.cleanup_warnings == [
        'Keycloak disable is pending. Retry this operation.'
    ]
    adapter.set_enabled.side_effect = None
    assert not (await service.disable_user(str(user.id))).cleanup_warnings


async def test_enable_failure_preserves_disabled_state_and_role(
    accounts_db, monkeypatch
):
    user = await account(accounts_db)
    service = AdminUserLifecycleService(session_factory=accounts_db)
    await service.disable_user(str(user.id))
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    adapter = AsyncMock()
    adapter.set_enabled.side_effect = RuntimeError('upstream unavailable')
    monkeypatch.setattr(service, '_keycloak', lambda: adapter)
    with pytest.raises(RuntimeError):
        await service.enable_user(str(user.id))
    async with accounts_db() as session:
        stored = await session.get(User, user.id)
        assert stored.is_disabled
        assert stored.role_id is None


async def test_final_active_superadmin_counts_only_active_accounts(accounts_db):
    admin = await account(accounts_db, 'admin@example.com', instance_admin=True)
    disabled_admin = await account(
        accounts_db, 'disabled@example.com', instance_admin=True
    )
    async with accounts_db() as session, session.begin():
        (await session.get(User, disabled_admin.id)).is_disabled = True
    service = AdminUserLifecycleService(session_factory=accounts_db)
    for operation in (service.disable_user, service.delete_user):
        with pytest.raises(LastSuperAdminError):
            await operation(str(admin.id))
    assert (
        await UserStore.revoke_super_admin(str(admin.id))
        == SuperAdminRevokeResult.LAST_SUPER_ADMIN
    )
    assert (
        await UserStore.revoke_super_admin(str(disabled_admin.id))
        == SuperAdminRevokeResult.REVOKED
    )


async def test_delete_pending_keeps_identity_and_retries_remote_cleanup(
    accounts_db, monkeypatch
):
    user = await account(accounts_db)
    service = AdminUserLifecycleService(session_factory=accounts_db)
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    adapter = AsyncMock()
    adapter.delete.side_effect = RuntimeError('unavailable')
    monkeypatch.setattr(service, '_keycloak', lambda: adapter)
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.delete_user', AsyncMock()
    )
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.delete_team', AsyncMock()
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(service, '_delete_user_data', cleanup)
    result = await service.delete_user(str(user.id))
    assert result.cleanup_warnings
    cleanup.assert_not_awaited()
    async with accounts_db() as session:
        assert (await session.get(User, user.id)).is_disabled
    adapter.delete.side_effect = None
    result = await service.delete_user(str(user.id))
    assert not result.cleanup_warnings
    assert adapter.delete.await_count == 2
    cleanup.assert_awaited_once_with(str(user.id))


async def test_unverified_local_email_requires_invitation_secret(accounts_db):
    inviter = await account(accounts_db, 'inviter@example.com')
    user = await account(accounts_db)
    team = Org(id=uuid4(), name='invited-team')
    invitation = OrgInvitation(
        org_id=team.id,
        email=user.email,
        token='secret-token',
        role_id=2,
        inviter_id=inviter.id,
        status='pending',
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
    )
    async with accounts_db() as session, session.begin():
        session.add(team)
        await session.flush()
        session.add(invitation)
    assert await OrgInvitationService.accept_pending_invitations_for_user(user) == []
    accepted = await OrgInvitationService.accept_invitation('secret-token', user.id)
    assert accepted.accepted_by_user_id == user.id
    async with accounts_db() as session:
        stored = await session.get(User, user.id)
        assert stored.email_verified
        assert stored.current_org_id == team.id
        assert (await session.get(OrgMember, (team.id, user.id))).role_id == 2
    assert (
        await OrgInvitationService.accept_invitation('secret-token', user.id)
    ).id == accepted.id


async def test_atomic_local_provision_rolls_back_generated_credential_if_key_creation_fails(
    accounts_db, monkeypatch
):
    from storage.api_key_store import ApiKeyStore

    admin = await account(accounts_db, 'admin@example.com', instance_admin=True)
    team = Org(id=uuid4(), name='atomic-team')
    async with accounts_db() as session, session.begin():
        session.add(team)
    service = EnterpriseUserManagementService(accounts_db)
    kwargs = dict(
        actor=actor(admin),
        organization_id=team.id,
        role_name='member',
        api_key_name='Initial API Key',
    )
    with monkeypatch.context() as failure:
        failure.setattr(
            ApiKeyStore,
            'generate_api_key',
            lambda self: (_ for _ in ()).throw(RuntimeError('key creation failed')),
        )
        with pytest.raises(RuntimeError):
            await service.provision('new@example.com', PASSWORD, **kwargs)
    assert await service.search('new@example.com') == []
    async with accounts_db() as session:
        assert (
            await session.scalar(select(func.count()).select_from(LocalCredentials))
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(Org)) == 2
    profile, created, added, key = await service.provision(
        'new@example.com', PASSWORD, **kwargs
    )
    assert created and added and key.startswith('sk-oh-')
    retried, created, added, retry_key = await service.provision(
        'new@example.com', SecretStr('Ignored replacement password'), **kwargs
    )
    assert retried.id == profile.id and not created and not added and retry_key == key
    assert (
        await LocalPasswordCredentialService(accounts_db).verify(
            profile.email, PASSWORD
        )
    ).id == profile.id


@pytest.mark.parametrize('custom_base', [None, 'https://member.example.com'])
async def test_direct_default_key_seeds_new_team_member_without_leaking_to_custom_host(
    accounts_db, monkeypatch, custom_base
):
    from server import constants
    from storage.org_store import OrgStore

    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', 'direct')
    monkeypatch.setattr(
        constants, 'OPENHANDS_DEFAULT_LLM_MODEL', 'openai/deployment-model'
    )
    monkeypatch.setattr(
        constants, 'OPENHANDS_DEFAULT_LLM_BASE_URL', 'https://deployment.example.com'
    )
    monkeypatch.setattr(constants, 'OPENHANDS_DEFAULT_LLM_API_KEY', 'deployment-secret')
    admin = await account(accounts_db, 'admin@example.com', instance_admin=True)
    team = Org(
        id=uuid4(),
        name='direct-team',
        **OrgStore.get_kwargs_from_settings(UserStore.default_settings()),
    )
    async with accounts_db() as session, session.begin():
        session.add(team)
    service = EnterpriseUserManagementService(accounts_db)
    user, _, _, _ = await service.provision(
        'new@example.com',
        PASSWORD,
        actor=actor(admin),
        organization_id=team.id,
        role_name='member',
        api_key_name='Initial API Key',
    )
    if custom_base:
        async with accounts_db() as session, session.begin():
            member = await session.get(OrgMember, (team.id, user.id))
            member.agent_settings_diff = {'llm': {'base_url': custom_base}}
    await service.ensure_llm_provisioned(user.id, team.id)
    async with accounts_db() as session:
        org = await session.get(Org, team.id)
        member = await session.get(OrgMember, (team.id, user.id))
        assert 'api_key' not in org.agent_settings['llm']
        assert member.llm_api_key.get_secret_value() == (
            '' if custom_base else 'deployment-secret'
        )
        assert not member.has_custom_llm_api_key
