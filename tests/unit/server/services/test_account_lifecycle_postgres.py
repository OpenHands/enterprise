"""Lifecycle serialization and workspace reset against migrated PostgreSQL 17."""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from server.auth import mode
from server.auth.contracts import InvalidCredentials
from server.auth.local.credentials import LocalPasswordCredentialService
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
)
from storage.api_key import ApiKey
from storage.auth_sessions import AuthSession
from storage.local_credentials import LocalCredentials
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.org_store import OrgStore
from storage.role import Role
from storage.user import User
from storage.user_store import SuperAdminRevokeResult, UserStore
from tests.unit.server.auth.test_local_auth_postgres import (
    PASSWORD,
    database,
    database_name,
    make_user,
    migration_logs,
    postgres_template,
)

# Shared migration fixtures intentionally imported; no duplicate test database API.
__all__ = ['database', 'database_name', 'migration_logs', 'postgres_template']
pytestmark = pytest.mark.skipif(
    not os.environ.get('OH_AUTH_TEST_POSTGRES_PORT'),
    reason='Requires isolated PostgreSQL 17 port',
)


@pytest.fixture
async def lifecycle_db(database, monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    for module in (
        'storage.api_key_store',
        'storage.user_store',
        'storage.org_store',
        'storage.role_store',
        'storage.org_member_store',
        'storage.org_invitation_store',
        'server.services.org_invitation_service',
        'server.services.admin_user_lifecycle_service',
        'server.auth.user_management',
    ):
        monkeypatch.setattr(module + '.a_session_maker', database)
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.delete_user', AsyncMock()
    )
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.delete_team', AsyncMock()
    )
    return database


async def test_personal_reset_preserves_final_admin_password_and_browser_session(
    lifecycle_db,
):
    from storage.jira_workspace import JiraWorkspace

    user = await make_user(
        lifecycle_db, instance_admin=True, must_change_password=False
    )
    session = await LocalPasswordCredentialService(lifecycle_db).login(
        user.email, PASSWORD
    )
    async with lifecycle_db() as db, db.begin():
        before = (await db.get(LocalCredentials, user.id)).password_hash
        (await db.get(Org, user.id)).contact_name = 'Existing name'
        db.add(ApiKey(user_id=str(user.id), org_id=user.id, key='workspace-key'))
        db.add(
            JiraWorkspace(
                name='workspace-integration',
                org_id=user.id,
                jira_cloud_id='jira-cloud',
                admin_user_id=str(user.id),
                webhook_secret='test',
                svc_acc_email='service@example.com',
                svc_acc_api_key='test',
                status='active',
            )
        )

    await OrgStore.delete_org_cascade(user.id, requester_user_id=str(user.id))
    async with lifecycle_db() as db:
        saved = await db.get(User, user.id)
        assert saved.id == saved.current_org_id == user.id
        assert saved.role_id == user.role_id and not saved.is_disabled
        assert (await db.get(LocalCredentials, user.id)).password_hash == before
        assert await db.get(AuthSession, session.principal.session_id)
        assert await db.get(Org, user.id)
        assert (await db.scalar(select(JiraWorkspace))).org_id is None
        membership = await db.get(OrgMember, (user.id, user.id))
        assert (await db.get(Role, membership.role_id)).name == 'owner'
        assert (
            await db.scalar(select(ApiKey).where(ApiKey.user_id == str(user.id)))
            is None
        )
    assert (
        await LocalPasswordCredentialService(lifecycle_db).verify(user.email, PASSWORD)
    ).id == user.id


async def test_workspace_reset_rolls_back_all_local_changes_on_cleanup_failure(
    lifecycle_db, monkeypatch
):
    user = await make_user(lifecycle_db)
    async with lifecycle_db() as db, db.begin():
        db.add(ApiKey(user_id=str(user.id), org_id=user.id, key='must-survive'))
        saved_hash = (await db.get(LocalCredentials, user.id)).password_hash
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.delete_team',
        AsyncMock(side_effect=RuntimeError('cleanup unavailable')),
    )
    with pytest.raises(RuntimeError):
        await OrgStore.delete_org_cascade(user.id, requester_user_id=str(user.id))
    async with lifecycle_db() as db:
        assert await db.get(User, user.id)
        assert await db.get(Org, user.id)
        assert await db.get(OrgMember, (user.id, user.id))
        assert (await db.get(LocalCredentials, user.id)).password_hash == saved_hash
        assert await db.scalar(select(ApiKey).where(ApiKey.key == 'must-survive'))


async def test_explicit_deletion_removes_account_and_credentials_with_shared_membership(
    lifecycle_db,
):
    user = await make_user(lifecycle_db)
    other = await make_user(lifecycle_db, 'other@example.com')
    await OrgMemberStore.add_user_to_org(other.id, user.id, 3, '', status='active')
    session = await LocalPasswordCredentialService(lifecycle_db).login(
        user.email, PASSWORD
    )
    result = await AdminUserLifecycleService(session_factory=lifecycle_db).delete_user(
        str(user.id)
    )
    assert not result.cleanup_warnings
    async with lifecycle_db() as db:
        assert await db.get(User, user.id) is None
        assert await db.get(LocalCredentials, user.id) is None
        assert await db.get(AuthSession, session.principal.session_id) is None
        assert await db.get(Org, user.id) is None
        assert await db.get(OrgMember, (other.id, user.id)) is None
        assert await db.get(User, other.id)


@pytest.mark.parametrize(
    'first,second',
    [('disable', 'disable'), ('disable', 'demote'), ('delete', 'demote')],
)
async def test_final_active_admin_survives_concurrent_removal(
    lifecycle_db, first, second
):
    users = [
        await make_user(lifecycle_db, f'admin{index}@example.com', instance_admin=True)
        for index in range(2)
    ]
    service = AdminUserLifecycleService(session_factory=lifecycle_db)

    async def remove(user, operation):
        if operation == 'demote':
            return await UserStore.revoke_super_admin(str(user.id))
        return await getattr(service, operation + '_user')(str(user.id))

    results = await asyncio.gather(
        remove(users[0], first), remove(users[1], second), return_exceptions=True
    )
    assert any(
        isinstance(result, LastSuperAdminError)
        or result == SuperAdminRevokeResult.LAST_SUPER_ADMIN
        for result in results
    )
    async with lifecycle_db() as db:
        active = await db.scalar(
            select(func.count())
            .select_from(User)
            .join(Role)
            .where(Role.name == 'admin', User.is_disabled.is_(False))
        )
        assert active == 1


@pytest.mark.parametrize('operation', ['disable', 'delete'])
async def test_enable_waits_for_committed_denial_and_remote_reconciliation(
    lifecycle_db, monkeypatch, operation
):
    user = await make_user(lifecycle_db)
    service = AdminUserLifecycleService(session_factory=lifecycle_db)
    entered = asyncio.Event()
    release = asyncio.Event()
    events = []
    adapter = AsyncMock()

    async def remote(*args):
        events.append('denied')
        entered.set()
        await release.wait()

    if operation == 'delete':
        adapter.delete.side_effect = remote
    else:

        async def set_enabled(identifier, enabled):
            if enabled:
                events.append('enabled')
            else:
                await remote()

        adapter.set_enabled.side_effect = set_enabled
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    monkeypatch.setattr(service, '_keycloak', lambda: adapter)
    deny = asyncio.create_task(getattr(service, operation + '_user')(str(user.id)))
    await asyncio.wait_for(entered.wait(), 5)
    async with lifecycle_db() as db:
        assert (await db.get(User, user.id)).is_disabled
    with pytest.raises(InvalidCredentials):
        await LocalPasswordCredentialService(lifecycle_db).login(user.email, PASSWORD)
    enable = asyncio.create_task(service.enable_user(str(user.id)))
    try:
        await asyncio.sleep(0.15)
        assert not enable.done()
        assert events == ['denied']
    finally:
        release.set()
    denied_result, enabled_result = await asyncio.wait_for(
        asyncio.gather(deny, enable), 10
    )
    assert not denied_result.cleanup_warnings
    async with lifecycle_db() as db:
        saved = await db.get(User, user.id)
        if operation == 'delete':
            assert saved is None and enabled_result is None
        else:
            assert not saved.is_disabled and events == ['denied', 'enabled']


async def test_membership_removal_waits_for_workspace_reset(lifecycle_db, monkeypatch):
    user = await make_user(lifecycle_db)
    other = await make_user(lifecycle_db, 'other@example.com')
    await OrgMemberStore.add_user_to_org(other.id, user.id, 3, '', status='active')
    entered = asyncio.Event()
    release = asyncio.Event()

    async def cleanup(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr('storage.lite_llm_manager.LiteLlmManager.delete_team', cleanup)
    reset = asyncio.create_task(
        OrgStore.delete_org_cascade(user.id, requester_user_id=str(user.id))
    )
    await asyncio.wait_for(entered.wait(), 5)
    removal = asyncio.create_task(
        OrgMemberStore.remove_user_from_org(other.id, user.id)
    )
    try:
        await asyncio.sleep(0.15)
        assert not removal.done()
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(reset, removal), 10)
    async with lifecycle_db() as db:
        assert (await db.get(User, user.id)).current_org_id == user.id
        assert await db.get(OrgMember, (user.id, user.id))


@pytest.mark.parametrize('system', [False, True])
async def test_api_key_issuance_waits_for_disable_and_rechecks_account(
    lifecycle_db, system
):
    from storage.api_key_store import ApiKeyStore

    user = await make_user(lifecycle_db)
    store = ApiKeyStore.get_instance()
    async with lifecycle_db() as db, db.begin():
        locked = await db.scalar(
            select(User).where(User.id == user.id).with_for_update()
        )
        locked.is_disabled = True
        await db.flush()
        operation = (
            store.get_or_create_system_api_key(str(user.id), user.id, 'internal')
            if system
            else store.create_api_key(str(user.id), org_id=user.id)
        )
        issuance = asyncio.create_task(operation)
        await asyncio.sleep(0.15)
        assert not issuance.done()
    with pytest.raises(ValueError, match='disabled'):
        await asyncio.wait_for(issuance, 5)
    async with lifecycle_db() as db:
        assert (
            await db.scalar(select(ApiKey).where(ApiKey.user_id == str(user.id)))
            is None
        )


async def test_other_orphan_is_protected_by_real_foreign_key_transaction(lifecycle_db):
    from server.routes.org_models import OrphanedUserError

    requester = await make_user(lifecycle_db)
    other = await make_user(lifecycle_db, 'other@example.com')
    async with lifecycle_db() as db, db.begin():
        (await db.get(User, other.id)).current_org_id = requester.id
        await db.delete(await db.get(OrgMember, (other.id, other.id)))
        db.add(
            OrgMember(
                org_id=requester.id,
                user_id=other.id,
                role_id=3,
                status='active',
                llm_api_key=PASSWORD,
                agent_settings_diff={},
                conversation_settings_diff={},
            )
        )
    with pytest.raises(OrphanedUserError):
        await OrgStore.delete_org_cascade(
            requester.id, requester_user_id=str(requester.id)
        )
    async with lifecycle_db() as db:
        assert await db.get(User, other.id)
        assert await db.get(Org, requester.id)
        assert await db.get(OrgMember, (requester.id, other.id))


async def test_legacy_hydration_preserves_account_settings_and_existing_api_key(
    lifecycle_db, monkeypatch
):
    from datetime import UTC, datetime
    from uuid import uuid4

    from server.auth.keycloak.account_management import KeycloakAccountManagement
    from storage.encrypt_utils import encrypt_legacy_value
    from storage.user_settings import UserSettings

    identifier = uuid4()
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    monkeypatch.setattr(
        'server.auth.keycloak.account_management.a_session_maker', lifecycle_db
    )
    monkeypatch.setattr(
        UserStore, '_acquire_user_creation_lock', AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        UserStore, '_release_user_creation_lock', AsyncMock(return_value=True)
    )
    monkeypatch.setattr('integrations.stripe_service.migrate_customer', AsyncMock())
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.migrate_entries', AsyncMock()
    )
    async with lifecycle_db() as db, db.begin():
        db.add(
            UserSettings(
                keycloak_user_id=str(identifier),
                already_migrated=False,
                email='legacy@example.com',
                email_verified=True,
                accepted_tos=datetime.now(UTC).replace(tzinfo=None),
                language='es',
                llm_api_key=encrypt_legacy_value('legacy-private-llm-key'),
                agent_settings={
                    'llm': {
                        'model': 'openai/legacy-custom',
                        'base_url': 'https://legacy.example.com',
                    }
                },
                conversation_settings={},
            )
        )
        db.add(
            ApiKey(user_id=str(identifier), key='stable-existing-api-key', org_id=None)
        )
    assert await UserStore.get_user_by_id(str(identifier)) is None
    tokens = AsyncMock()
    tokens.get_user_info_from_user_id.return_value = {
        'email': 'legacy@example.com',
        'email_verified': True,
    }
    user = await KeycloakAccountManagement(tokens).hydrate(identifier)
    assert user.id == user.current_org_id == identifier
    assert user.language == 'es'
    async with lifecycle_db() as db:
        member = await db.get(OrgMember, (identifier, identifier))
        assert member.llm_api_key.get_secret_value() == 'legacy-private-llm-key'
        assert member.agent_settings_diff['llm']['model'] == 'openai/legacy-custom'
        key = await db.scalar(
            select(ApiKey).where(ApiKey.key == 'stable-existing-api-key')
        )
        assert key.user_id == str(identifier) and key.org_id == identifier
        legacy = await db.scalar(
            select(UserSettings).where(UserSettings.keycloak_user_id == str(identifier))
        )
        assert legacy.already_migrated
