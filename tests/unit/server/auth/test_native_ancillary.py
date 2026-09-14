"""Native lifecycle and external work cross their real PostgreSQL boundaries."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import JsonValue, SecretStr, TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from server.auth import ancillary_config, auth_config, bootstrap, keycloak_manager
from server.auth.native_password import NativeAuthError
from server.auth.native_types import SessionFactory
from server.services import admin_user_lifecycle_service, native_auth_service
from server.services.admin_user_lifecycle_service import AdminUserLifecycleService
from server.services.native_provisioning_service import (
    NativeProvisionedKeys,
    NativeProvisioningRequest,
    NativeProvisioningService,
)
from server.services.org_invitation_service import OrgInvitationService
from storage import database, org_store, role_store, saas_settings_store, user_store
from storage.native_auth import (
    AuthAccount,
    PasswordCredential,
)
from storage.native_external_work import NativeExternalPayload, NativeExternalWork
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from storage.user_authorization import UserAuthorizationType
from storage.user_authorization_store import UserAuthorizationStore
from storage.user_settings import UserSettings
from tests.unit.server.auth.native_test_types import (
    NativeFixture,
    present,
)
from tests.unit.server.auth.test_native_auth_foundation import (
    PASSWORD,  # noqa: F401
    enroll,
    link_token,
)

pytest_plugins = ['tests.unit.server.auth.test_native_auth_foundation']


@pytest.fixture
async def integrated(
    native: NativeFixture, configured: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[NativeFixture]:
    from server.services import org_invitation_service

    for module in (
        user_store,
        admin_user_lifecycle_service,
        org_invitation_service,
        ancillary_config,
    ):
        monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
    for module in (
        bootstrap,
        database,
        user_store,
        org_store,
        role_store,
        saas_settings_store,
        admin_user_lifecycle_service,
    ):
        monkeypatch.setattr(module, 'a_session_maker', configured)
    service, admin_id = native
    monkeypatch.setattr(native_auth_service, 'get_native_auth_service', lambda: service)
    with (
        patch.object(
            keycloak_manager,
            'KeycloakOpenID',
            side_effect=AssertionError('Keycloak constructed'),
        ),
        patch.object(
            keycloak_manager,
            'KeycloakAdmin',
            side_effect=AssertionError('Keycloak constructed'),
        ),
    ):
        yield service, admin_id


async def test_native_store_never_reconstructs_legacy_profile(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown = uuid4()
    async with configured() as session, session.begin():
        session.add(UserSettings(keycloak_user_id=str(unknown), already_migrated=False))
    creation_lock = AsyncMock(side_effect=AssertionError('Legacy lock consulted'))
    monkeypatch.setattr(
        user_store.UserStore, '_acquire_user_creation_lock', creation_lock
    )
    assert await user_store.UserStore.get_user_by_id(str(unknown)) is None
    with pytest.raises(NativeAuthError):
        await user_store.UserStore.create_user(
            str(unknown), {'email': 'intruder@example.test'}
        )
    with pytest.raises(NativeAuthError):
        await user_store.UserStore.downgrade_user(str(unknown))
    with pytest.raises(NativeAuthError):
        await user_store.UserStore.migrate_user(str(unknown), UserSettings(), {})


async def test_lifecycle_actor_and_last_admin_revalidated(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = integrated
    second = await enroll(service, admin_id)
    second_id = second.principal.account_id
    await user_store.UserStore.grant_super_admin(
        str(second_id), actor_user_id=str(admin_id)
    )
    await user_store.UserStore.revoke_super_admin(
        str(second_id), actor_user_id=str(admin_id)
    )
    with pytest.raises(NativeAuthError) as denied:
        await AdminUserLifecycleService().disable_user(
            str(admin_id), actor_user_id=str(second_id)
        )
    assert denied.value.status_code == 403
    with pytest.raises(NativeAuthError) as last:
        await AdminUserLifecycleService().disable_user(
            str(admin_id), actor_user_id=str(admin_id)
        )
    assert last.value.status_code == 409
    with pytest.raises(NativeAuthError) as self_delete:
        await org_store.OrgStore.delete_org_cascade(admin_id, str(admin_id))
    assert self_delete.value.status_code == 409


async def test_native_self_delete_preserves_uuid_and_requires_cleanup(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = integrated
    login = await enroll(service, admin_id)
    account_id = login.principal.account_id
    await user_store.UserStore.grant_super_admin(
        str(account_id), actor_user_id=str(admin_id)
    )
    await org_store.OrgStore.delete_org_cascade(account_id, str(account_id))
    assert await service.authenticate_session(login.token) is None
    assert await user_store.UserStore.get_user_by_id(str(account_id)) is None
    async with configured() as session:
        assert (
            present(await session.get(AuthAccount, account_id))
        ).state == 'reonboardable'
        assert await session.get(PasswordCredential, account_id) is not None
    with pytest.raises(NativeAuthError) as cleanup_pending:
        await service.login('person@example.test', PASSWORD, client_ip='1')
    assert cleanup_pending.value.status_code == 503
    cleanup_calls = []

    async def cleanup(
        kind: str,
        user_id: UUID | None,
        org_id: UUID | None,
        payload: NativeExternalPayload,
    ) -> None:
        cleanup_calls.append((kind, user_id, org_id))

    monkeypatch.setattr(
        'server.services.native_litellm_adapter.cleanup_native_resource', cleanup
    )
    assert (await NativeProvisioningService(configured).cleanup())[1] == 0
    fresh = await service.login('person@example.test', PASSWORD, client_ip='2')
    assert fresh.principal.account_id == account_id
    async with configured() as session:
        assert (present(await session.get(User, account_id))).role_id is None
    count = len(cleanup_calls)
    await NativeProvisioningService(configured).cleanup()
    assert len(cleanup_calls) == count


async def test_profileless_disable_enable_and_terminal_delete(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = integrated
    login = await enroll(service, admin_id)
    account_id = login.principal.account_id
    await org_store.OrgStore.delete_org_cascade(account_id, str(account_id))
    lifecycle = AdminUserLifecycleService()
    await lifecycle.disable_user(str(account_id), actor_user_id=str(admin_id))
    async with configured() as session:
        assert (
            present(await session.get(AuthAccount, account_id))
        ).state == 'profile_absent_blocked'
    with pytest.raises(NativeAuthError) as denied:
        await service.login('person@example.test', PASSWORD, client_ip='1')
    assert denied.value.status_code == 401
    await lifecycle.enable_user(str(account_id), actor_user_id=str(admin_id))
    deleted = await lifecycle.delete_user(str(account_id), actor_user_id=str(admin_id))
    assert deleted is not None and not deleted.cleanup_warnings
    async with configured() as session:
        assert (present(await session.get(AuthAccount, account_id))).state == 'deleted'
        assert await session.get(PasswordCredential, account_id) is None
    with pytest.raises(NativeAuthError):
        await lifecycle.enable_user(str(account_id), actor_user_id=str(admin_id))


async def test_provisioning_does_not_hold_lifecycle_lock_and_discards_disabled_result(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = integrated
    login = await enroll(service, admin_id)
    account_id = login.principal.account_id
    # Isolate the target's job; bootstrap work is covered separately.
    async with configured() as session, session.begin():
        await session.execute(
            delete(NativeExternalWork).where(NativeExternalWork.account_id == admin_id)
        )
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        entered.set()
        await release.wait()
        return NativeProvisionedKeys(member_key=request.member_key)

    reconcile = asyncio.create_task(
        NativeProvisioningService(configured).reconcile(provider)
    )
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(
        AdminUserLifecycleService().disable_user(
            str(account_id), actor_user_id=str(admin_id)
        ),
        2,
    )
    release.set()
    assert await reconcile == (0, 0)
    async with configured() as session:
        member = await session.get(OrgMember, (account_id, account_id))
        assert present(member).llm_api_key.get_secret_value() == ''
        assert present(member).status == 'pending_llm_provisioning'
        work = await session.get(
            NativeExternalWork, present(member).native_provisioning_id
        )
        assert present(work).status == 'cleanup'
    cleanup = AsyncMock()
    monkeypatch.setattr(
        'server.services.native_litellm_adapter.cleanup_native_resource', cleanup
    )
    assert await NativeProvisioningService(configured).cleanup() == (1, 0)
    cleanup.assert_awaited_once()
    await AdminUserLifecycleService().enable_user(
        str(account_id), actor_user_id=str(admin_id)
    )
    release.set()
    assert await NativeProvisioningService(configured).reconcile(provider) == (1, 0)
    async with configured() as session:
        assert (present(await session.get(Org, account_id))).llm_api_key is None


async def test_expired_work_lease_reuses_secret_after_crash(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    _, admin_id = integrated
    async with configured() as session, session.begin():
        work = await session.scalar(
            select(NativeExternalWork).where(NativeExternalWork.account_id == admin_id)
        )
        original = present(work).payload['member_key']
        present(work).status = 'running'
        present(work).claim_id = uuid4()
        present(work).lease_until = datetime.now(UTC) - timedelta(seconds=1)

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        assert request.member_key.get_secret_value() == original
        return NativeProvisionedKeys(member_key=request.member_key)

    assert await NativeProvisioningService(configured).reconcile(provider) == (1, 0)


async def test_membership_invitation_is_exact_local_atomic_and_not_automatic(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = integrated
    login = await enroll(service, admin_id)
    account_id = login.principal.account_id
    org_id = uuid4()
    async with configured() as session, session.begin():
        role = await session.scalar(select(Role).where(Role.name == 'member'))
        session.add(Org(id=org_id, name='Native team'))
        await session.flush()
        invitation = OrgInvitation(
            token='membership-only-token',
            org_id=org_id,
            email='PERSON@example.test',
            role_id=present(role).id,
            inviter_id=admin_id,
            status='pending',
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
        )
        session.add(invitation)
    assert (
        await OrgInvitationService.accept_pending_invitations_for_user(
            present(await user_store.UserStore.get_user_by_id(str(account_id)))
        )
        == []
    )
    accepted = await OrgInvitationService.accept_invitation(
        'membership-only-token', account_id
    )
    assert accepted.accepted_by_user_id == account_id
    async with configured() as session:
        member = await session.get(OrgMember, (org_id, account_id))
        assert present(member).status == 'pending_llm_provisioning'
        assert present(member).native_provisioning_id is not None


async def test_local_admission_applies_to_sessions_password_and_background(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = integrated
    login = await enroll(service, admin_id, 'person+alias@example.test')
    account_id = login.principal.account_id
    async with configured() as session, session.begin():
        await UserAuthorizationStore.create_authorization(
            '%@example.test', None, UserAuthorizationType.BLACKLIST, session
        )
        await UserAuthorizationStore.create_authorization(
            'admin@example.test', None, UserAuthorizationType.WHITELIST, session
        )
    assert await service.get_identity(account_id) is None
    assert await service.authenticate_session(login.token) is None
    with pytest.raises(NativeAuthError) as denied:
        await service.login('person+alias@example.test', PASSWORD, client_ip='1')
    assert denied.value.status_code == 401
    async with configured() as session, session.begin():
        # A base-email allow rule must not silently rewrite the native identity.
        await UserAuthorizationStore.create_authorization(
            'person@example.test', None, UserAuthorizationType.WHITELIST, session
        )
    assert await service.get_identity(account_id) is None
    async with configured() as session, session.begin():
        await UserAuthorizationStore.create_authorization(
            'person+alias@example.test', None, UserAuthorizationType.WHITELIST, session
        )
    assert await service.get_identity(account_id) is not None


@pytest.mark.parametrize('value', ['true', '1', 'TRUE'])
def test_unsupported_ancillary_features_fail_configuration(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setattr(ancillary_config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setenv('SLACK_WEBHOOKS_ENABLED', value)
    with pytest.raises(ValueError, match='Slack'):
        ancillary_config.validate_native_ancillary_config()
    monkeypatch.setattr(ancillary_config, 'ENABLE_KEYCLOAK', True)
    ancillary_config.validate_native_ancillary_config()


async def test_concurrent_native_self_revocations_preserve_an_active_admin(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = integrated
    second = await enroll(service, admin_id)
    second_id = second.principal.account_id
    await user_store.UserStore.grant_super_admin(
        str(second_id), actor_user_id=str(admin_id)
    )
    results = await asyncio.gather(
        AdminUserLifecycleService().disable_user(
            str(admin_id), actor_user_id=str(admin_id)
        ),
        user_store.UserStore.revoke_super_admin(
            str(second_id), actor_user_id=str(second_id)
        ),
        return_exceptions=True,
    )
    assert any(
        isinstance(result, NativeAuthError)
        or result == user_store.SuperAdminRevokeResult.LAST_SUPER_ADMIN
        for result in results
    )
    async with configured() as session:
        active = list(
            await session.scalars(
                select(User)
                .join(Role, Role.id == User.role_id)
                .where(User.is_disabled.is_(False), Role.name == 'admin')
            )
        )
        assert len(active) == 1


async def test_native_direct_defaults_settings_without_git_and_org_isolation(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import constants

    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', 'direct')
    monkeypatch.setattr(
        constants, 'OPENHANDS_DEFAULT_LLM_MODEL', 'openai/native-local-model'
    )
    monkeypatch.setattr(
        constants, 'OPENHANDS_DEFAULT_LLM_BASE_URL', 'http://localhost:11434/v1'
    )
    monkeypatch.setattr(constants, 'OPENHANDS_DEFAULT_LLM_API_KEY', 'direct-test-key')
    service, admin_id = integrated
    login = await enroll(service, admin_id)
    account_id = login.principal.account_id
    store = saas_settings_store.SaasSettingsStore(str(account_id))
    settings = await store.load()
    assert settings is not None
    assert settings.agent_settings.llm.model == 'openai/native-local-model'
    assert settings.agent_settings.llm.api_key == SecretStr('direct-test-key')
    assert (
        await saas_settings_store.SaasSettingsStore(
            str(account_id), effective_org_id=admin_id
        ).load()
        is None
    )
    async with configured() as session:
        assert (
            present(await session.get(AuthAccount, account_id))
        ).provisioning_status == 'complete'
        assert (present(await session.get(User, account_id))).email_verified is False
        from storage.native_git import GitConnection

        assert (
            await session.scalar(
                select(GitConnection).where(GitConnection.account_id == account_id)
            )
            is None
        )


async def test_native_queued_budget_and_managed_key_tasks_execute(
    integrated: NativeFixture,
    configured: SessionFactory,
    session_maker: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import run_maintenance_tasks
    from server.maintenance_task_processor import org_budget_maintenance_processor
    from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
        ManagedLlmKeyOwnershipProcessor,
        ManagedLlmKeyOwnershipTarget,
    )
    from server.maintenance_task_processor.org_budget_maintenance_processor import (
        OrgBudgetMaintenanceProcessor,
    )
    from server.services.org_budget_service import _current_cycle_start
    from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
    from storage.org_budget_settings import OrgBudgetSettings

    org_id = uuid4()
    _, admin_id = integrated
    async with configured() as session, session.begin():
        session.add(Org(id=org_id, name='Budget org'))
        await session.flush()
        session.add(
            OrgBudgetSettings(
                org_id=org_id,
                enabled=True,
                reset_day=1,
                monthly_limit=100,
                cycle_start_at=_current_cycle_start(
                    datetime.now(UTC) - timedelta(days=40), 1
                ),
                cycle_start_spend=0,
            )
        )
    with session_maker() as sync_session:
        for processor in (
            OrgBudgetMaintenanceProcessor(org_ids=[str(org_id)]),
            ManagedLlmKeyOwnershipProcessor(
                targets=[
                    ManagedLlmKeyOwnershipTarget(
                        org_id=str(admin_id), user_id=str(admin_id)
                    )
                ]
            ),
        ):
            task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
            task.set_processor(processor)
            sync_session.add(task)
        sync_session.commit()
    monkeypatch.setattr(run_maintenance_tasks, 'session_maker', session_maker)
    monkeypatch.setattr(org_budget_maintenance_processor, 'a_session_maker', configured)
    with (
        patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(
                return_value={'team_max_budget': 120, 'team_spend': 20, 'members': {}}
            ),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_team', AsyncMock()
        ),
    ):
        failed = await run_maintenance_tasks.run_tasks()
        with session_maker() as inspection:
            assert failed == 0, [
                t.info for t in inspection.scalars(select(MaintenanceTask))
            ]
    with session_maker() as sync_session:
        tasks = list(sync_session.scalars(select(MaintenanceTask)))
        assert all(t.status == MaintenanceTaskStatus.COMPLETED for t in tasks)
        assert {
            present(t.info).get('processed', present(t.info).get('skipped'))
            for t in tasks
        } == {1}


async def test_native_job_mode_mismatch_prevents_task_execution(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import run_maintenance_tasks
    from sync import clean_app_conversation_start_tasks

    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'keycloak')
    runner = AsyncMock(side_effect=AssertionError('Task execution began'))
    monkeypatch.setattr(run_maintenance_tasks, 'run_tasks', runner)
    with pytest.raises(RuntimeError, match='mode'):
        await run_maintenance_tasks.main()
    with pytest.raises(RuntimeError, match='mode'):
        await clean_app_conversation_start_tasks.main()
    runner.assert_not_called()


async def test_native_administrative_delete_revokes_profile_api_keys_atomically(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    from storage.api_key import ApiKey

    service, admin_id = integrated
    login = await enroll(service, admin_id)
    account_id = login.principal.account_id
    async with configured() as session, session.begin():
        session.add(
            ApiKey(
                user_id=str(account_id), org_id=account_id, key='delete-this-api-key'
            )
        )
    result = await AdminUserLifecycleService().delete_user(
        str(account_id), actor_user_id=str(admin_id)
    )
    assert result and not result.cleanup_warnings
    async with configured() as session:
        assert await session.get(User, account_id) is None
        assert (present(await session.get(AuthAccount, account_id))).state == 'deleted'
        assert (
            await session.scalar(
                select(ApiKey).where(ApiKey.user_id == str(account_id))
            )
            is None
        )
        assert await session.get(PasswordCredential, account_id) is None
    assert await service.authenticate_session(login.token) is None


async def test_native_litellm_adapter_retries_same_key_and_verifies_ownership(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.services import native_litellm_adapter
    from storage.lite_llm_manager import LiteLlmManager

    _, admin_id = integrated
    async with configured() as session:
        work = await session.scalar(
            select(NativeExternalWork).where(NativeExternalWork.account_id == admin_id)
        )
        key = present(work).payload['member_key']
    remote: dict[str, JsonValue] = {}
    generated: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/key/info':
            return (
                httpx.Response(200, json={'info': remote})
                if remote
                else httpx.Response(404)
            )
        assert request.url.path == '/key/generate'
        payload = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        generated.append(TypeAdapter(str).validate_python(payload['key']))
        remote.update(payload)
        # Simulate the server committing but the reply never reaching the worker.
        raise httpx.ReadTimeout('lost response')

    monkeypatch.setattr(
        native_litellm_adapter, 'LITE_LLM_API_URL', 'https://llm.example.test'
    )
    monkeypatch.setattr(
        native_litellm_adapter,
        '_client',
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(
        native_litellm_adapter, '_is_billing_enabled', AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        LiteLlmManager, '_get_team', AsyncMock(return_value={'team_info': {}})
    )
    monkeypatch.setattr(LiteLlmManager, '_create_user', AsyncMock(return_value=True))
    monkeypatch.setattr(LiteLlmManager, '_add_user_to_team', AsyncMock())
    reconciler = NativeProvisioningService(configured)
    assert await reconciler.reconcile() == (0, 1)
    assert await reconciler.reconcile() == (1, 0)
    assert generated == [key]
    async with configured() as session:
        member = await session.get(OrgMember, (admin_id, admin_id))
        assert present(member).llm_api_key.get_secret_value() == key
        assert (present(await session.get(Org, admin_id))).llm_api_key is None


async def test_disabled_work_does_not_starve_new_active_member(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = integrated
    disabled = await enroll(service, admin_id)
    disabled_id = disabled.principal.account_id
    await AdminUserLifecycleService().disable_user(
        str(disabled_id), actor_user_id=str(admin_id)
    )
    monkeypatch.setattr(
        'server.services.native_litellm_adapter.cleanup_native_resource', AsyncMock()
    )
    reconciler = NativeProvisioningService(configured)
    assert await reconciler.cleanup() == (1, 0)
    active = await enroll(service, admin_id, 'new@example.test')
    async with configured() as session, session.begin():
        await session.execute(
            delete(NativeExternalWork).where(NativeExternalWork.account_id == admin_id)
        )
        member = await session.get(OrgMember, (disabled_id, disabled_id))
        assert (
            present(
                await session.get(
                    NativeExternalWork, present(member).native_provisioning_id
                )
            )
        ).status == 'suspended'
    requests = []

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        requests.append(request)
        return NativeProvisionedKeys(member_key=request.member_key)

    assert await reconciler.reconcile(provider, limit=1) == (1, 0)
    assert requests[0].account_id == active.principal.account_id
    await AdminUserLifecycleService().enable_user(
        str(disabled_id), actor_user_id=str(admin_id)
    )
    assert await reconciler.reconcile(provider, limit=1) == (1, 0)
    assert requests[-1].account_id == disabled_id


async def test_blacklisted_admin_cannot_replace_last_usable_admin(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = integrated
    other = await enroll(service, admin_id)
    other_id = other.principal.account_id
    await user_store.UserStore.grant_super_admin(
        str(other_id), actor_user_id=str(admin_id)
    )
    async with configured() as session, session.begin():
        await UserAuthorizationStore.create_authorization(
            'person@example.test', None, UserAuthorizationType.BLACKLIST, session
        )
    with pytest.raises(NativeAuthError) as last:
        await AdminUserLifecycleService().disable_user(
            str(admin_id), actor_user_id=str(admin_id)
        )
    assert last.value.status_code == 409


async def test_terminal_delete_revokes_competing_old_enrollment_links(
    integrated: NativeFixture,
) -> None:
    service, admin_id = integrated
    old = await service.issue_invitation(admin_id, 'same@example.test')
    used = await service.issue_invitation(admin_id, 'SAME@example.test')
    login = await service.complete_enrollment(link_token(used), PASSWORD, client_ip='1')
    await AdminUserLifecycleService().delete_user(
        str(present(login).principal.account_id), actor_user_id=str(admin_id)
    )
    with pytest.raises(NativeAuthError):
        await service.complete_enrollment(link_token(old), PASSWORD, client_ip='1')
    fresh = await enroll(service, admin_id, 'same@example.test')
    assert present(fresh).principal.account_id != present(login).principal.account_id


async def test_custom_settings_win_against_pending_managed_callback(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    _, admin_id = integrated
    store = saas_settings_store.SaasSettingsStore(str(admin_id))
    provider_started = asyncio.Event()
    provider_finish = asyncio.Event()

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        provider_started.set()
        await provider_finish.wait()
        return NativeProvisionedKeys(member_key=request.member_key)

    reconciler = NativeProvisioningService(configured)
    provisioning = asyncio.create_task(reconciler.reconcile(provider))
    await provider_started.wait()
    settings = await store.load()
    present(settings).agent_settings.llm.model = 'openai/custom-model'
    present(settings).agent_settings.llm.base_url = 'https://custom.example.test/v1'
    present(settings).agent_settings.llm.api_key = SecretStr('user-custom-key')
    await asyncio.wait_for(store.store(present(settings)), timeout=2)
    provider_finish.set()
    assert await provisioning == (0, 0)
    async with configured() as session:
        member = await session.get(OrgMember, (admin_id, admin_id))
        assert present(member).has_custom_llm_api_key
        assert present(member).llm_api_key.get_secret_value() == 'user-custom-key'
        assert present(member).native_provisioning_id is None
    cleanup = AsyncMock()
    monkeypatch.setattr(
        'server.services.native_litellm_adapter.cleanup_native_resource', cleanup
    )
    assert await reconciler.cleanup() == (1, 0)
    assert present(cleanup.await_args).args[3]['member_key'] != 'user-custom-key'
    async with configured() as session:
        assert (
            present(await session.get(OrgMember, (admin_id, admin_id)))
        ).llm_api_key.get_secret_value() == 'user-custom-key'


async def test_native_managed_settings_and_rotation_use_durable_generations(
    integrated: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.services import native_litellm_adapter, native_provisioning_service
    from storage.lite_llm_manager import LiteLlmManager

    monkeypatch.setattr(native_provisioning_service, 'a_session_maker', configured)
    _, admin_id = integrated
    store = saas_settings_store.SaasSettingsStore(str(admin_id))
    monkeypatch.setattr(
        LiteLlmManager,
        'delete_key_by_alias',
        AsyncMock(side_effect=AssertionError('Legacy alias deletion')),
    )
    monkeypatch.setattr(
        LiteLlmManager,
        'generate_key',
        AsyncMock(side_effect=AssertionError('Legacy generation')),
    )
    settings = await store.load()
    await store.store(present(settings))
    requests = []

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        requests.append(request)
        return NativeProvisionedKeys(member_key=request.member_key)

    monkeypatch.setattr(native_litellm_adapter, 'provision_native_member', provider)
    reconciler = NativeProvisioningService(configured)
    assert await reconciler.reconcile() == (1, 0)
    first_key = requests[-1].member_key.get_secret_value()
    result = await store.rotate_managed_llm_key()
    assert result.status == 'rotated'
    second_key = requests[-1].member_key.get_secret_value()
    assert first_key != second_key
    cleanup = AsyncMock()
    monkeypatch.setattr(native_litellm_adapter, 'cleanup_native_resource', cleanup)
    assert await reconciler.cleanup() == (1, 0)
    assert present(cleanup.await_args).args[3]['member_key'] == first_key
    async with configured() as session:
        member = await session.get(OrgMember, (admin_id, admin_id))
        assert present(member).llm_api_key.get_secret_value() == second_key
        assert (present(await session.get(Org, admin_id))).llm_api_key is None


async def test_native_git_claim_rejects_alternate_host_before_lookup(
    integrated: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from server.auth import native_git_config
    from server.routes import orgs
    from server.routes.org_models import GitOrgClaimRequest
    from server.services import native_git_credentials

    _, admin_id = integrated
    monkeypatch.setattr(
        native_git_config,
        'git_config',
        lambda provider: native_git_config.NativeGitConfig(provider, 'github.com', ()),
    )
    from openhands.app_server.integrations.provider import ProviderToken
    from server.services.native_git_credentials import NativeGitCredentialService

    service = NativeGitCredentialService()
    monkeypatch.setattr(
        service,
        'get_token',
        AsyncMock(return_value=ProviderToken(host='git.example.test')),
    )
    monkeypatch.setattr(
        native_git_credentials, 'get_native_git_service', lambda: service
    )
    lookup = AsyncMock(side_effect=AssertionError('Hostless claim store reached'))
    monkeypatch.setattr(
        orgs.OrgGitClaimStore, 'get_claim_by_provider_and_git_org', lookup
    )
    with pytest.raises(HTTPException) as denied:
        await orgs.claim_git_organization(
            admin_id,
            GitOrgClaimRequest(provider='github', git_organization='example'),
            user_id=str(admin_id),
        )
    assert denied.value.status_code == 409
    lookup.assert_not_called()


async def test_native_org_default_switch_queues_each_member_key(
    integrated: NativeFixture, configured: SessionFactory
) -> None:
    from server.routes.org_models import OrgUpdate
    from server.services.native_account_service import (
        add_membership,
        lock_native_lifecycle,
    )

    service, admin_id = integrated
    other = await enroll(service, admin_id)
    other_id = other.principal.account_id
    async with configured() as session, session.begin():
        await lock_native_lifecycle(session)
        role = await session.scalar(select(Role).where(Role.name == 'member'))
        await add_membership(
            session,
            present(await session.get(User, other_id)),
            present(await session.get(Org, admin_id)),
            present(role).id,
        )
    await org_store.OrgStore.update_org(
        admin_id,
        OrgUpdate(
            llm_api_key='shared-direct-key',
            agent_settings_diff={
                'llm': {
                    'model': 'openai/direct-model',
                    'base_url': 'https://direct.example.test/v1',
                }
            },
        ),
        str(admin_id),
    )
    async with configured() as session:
        for user_id in (admin_id, other_id):
            member = await session.get(OrgMember, (admin_id, user_id))
            assert present(member).native_provisioning_id is None
            assert present(member).llm_api_key.get_secret_value() == 'shared-direct-key'
    await org_store.OrgStore.update_org(
        admin_id,
        OrgUpdate(
            llm_api_key='',
            agent_settings_diff={
                'llm': {'model': 'openhands/claude-sonnet-4', 'base_url': None}
            },
        ),
        str(admin_id),
    )
    async with configured() as session:
        members = list(
            await session.scalars(select(OrgMember).where(OrgMember.org_id == admin_id))
        )
        work_ids = [member.native_provisioning_id for member in members]
        assert len(set(work_ids)) == 2
        assert all(member.status == 'pending_llm_provisioning' for member in members)

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        return NativeProvisionedKeys(member_key=request.member_key)

    for work_id in work_ids:
        assert await NativeProvisioningService(configured).reconcile(
            provider, only_work_id=work_id
        ) == (1, 0)
    async with configured() as session:
        keys = {
            (
                present(await session.get(OrgMember, (admin_id, user_id)))
            ).llm_api_key.get_secret_value()
            for user_id in (admin_id, other_id)
        }
        assert len(keys) == 2 and 'shared-direct-key' not in keys
        assert (present(await session.get(Org, admin_id))).llm_api_key is None
