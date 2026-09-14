"""Native accounts remain usable without any managed gateway configuration."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from openhands.app_server.utils.litellm_integration import LiteLLMIntegrationDisabled
from server import constants
from server.auth.bootstrap import initialize_auth_installation
from server.auth.native_password import NativeAuthError
from server.auth.native_types import SessionFactory
from server.services.native_account_service import lock_native_lifecycle
from server.services.native_auth_service import NativeAuthService
from server.services.native_litellm_adapter import (
    cleanup_native_resource,
    provision_native_member,
)
from server.services.native_provisioning_service import (
    NativeProvisioningRequest,
    NativeProvisioningService,
    prepare_managed_member,
)
from storage.native_auth import AuthAccount, AuthInstallation, PasswordCredential
from storage.native_external_work import NativeExternalWork
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_store import OrgStore
from storage.saas_settings_store import SaasSettingsStore
from storage.user import User
from tests.unit.server.auth.native_test_types import (
    ConfiguredSamlIdentity,
    DisabledNative,
    FederatedClaims,
    present,
)
from tests.unit.server.auth.test_native_auth_foundation import PASSWORD, enroll

pytest_plugins = ['tests.unit.server.auth.test_native_auth_foundation']


@pytest.fixture
async def disabled_native(
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> DisabledNative:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    monkeypatch.setenv(
        'OPENHANDS_DEFAULT_ORG_ENABLED',
        'true' if getattr(request, 'param', False) else 'false',
    )
    monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_AUTO_ADD_USERS', 'true')
    for name in (
        'OPENHANDS_DEFAULT_LLM_MODEL',
        'OPENHANDS_DEFAULT_LLM_BASE_URL',
        'OPENHANDS_DEFAULT_LLM_API_KEY',
        'LLM_MODEL',
        'LLM_BASE_URL',
        'LLM_API_KEY',
    ):
        monkeypatch.delenv(name, raising=False)
        if hasattr(constants, name):
            monkeypatch.setattr(constants, name, None)
    for name in (
        'storage.database',
        'storage.org_store',
        'storage.org_member_store',
        'storage.role_store',
        'storage.user_store',
        'storage.saas_settings_store',
        'server.auth.bootstrap',
        'server.services.native_auth_service',
        'server.services.native_provisioning_service',
        'server.services.admin_user_lifecycle_service',
    ):
        module = import_module(name)
        monkeypatch.setattr(module, 'a_session_maker', configured)
        if hasattr(module, 'ENABLE_KEYCLOAK'):
            monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
    from server.services import (
        native_auth_service,
        native_maintenance_service,
        native_saml_service,
    )

    service = NativeAuthService(configured)
    monkeypatch.setattr(native_auth_service, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(
        native_saml_service,
        'get_native_saml_service',
        lambda: native_saml_service.NativeSamlService(configured),
    )
    monkeypatch.setattr(native_maintenance_service, 'ENABLE_KEYCLOAK', False)
    # Load runtime type annotations before replacing the shared httpx class.
    import_module('openhands.app_server.config')
    http_client = Mock(side_effect=AssertionError('Disabled gateway attempted HTTP'))
    monkeypatch.setattr(
        'server.services.native_litellm_adapter.httpx.AsyncClient', http_client
    )
    await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        installation = await session.get(AuthInstallation, 1)
        admin_id = present(present(installation).bootstrap_account_id)
    return DisabledNative(
        sessions=configured, service=service, admin_id=admin_id, http=http_client
    )


@pytest.mark.parametrize('disabled_native', [False, True], indirect=True)
async def test_keyless_bootstrap_and_enrollment_need_no_gateway(
    disabled_native: DisabledNative, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = disabled_native
    person = await enroll(native.service, native.admin_id)
    monkeypatch.setenv(
        'SUPERADMIN_PASSWORD', 'Changed bootstrap password stays ignored'
    )
    await initialize_auth_installation(session_factory=native.sessions)
    login = await native.service.login('admin@example.test', PASSWORD, client_ip='1')
    assert login.principal.account_id == native.admin_id
    assert login.redirect_to.startswith('/accept-tos')
    async with native.sessions() as session:
        accounts = (await session.scalars(select(AuthAccount))).all()
        assert len(accounts) == 2
        assert all(account.provisioning_status == 'complete' for account in accounts)
        members = (await session.scalars(select(OrgMember))).all()
        assert all(member.status == 'active' for member in members)
        assert all(member.native_provisioning_id is None for member in members)
        assert all(not member.llm_api_key.get_secret_value() for member in members)
        assert (
            await session.scalar(select(func.count()).select_from(NativeExternalWork))
            == 0
        )
        personal_org = await session.get(Org, person.principal.account_id)
        assert present(personal_org).contact_name == 'person@example.test'
    native.http.assert_not_called()


@pytest.mark.parametrize('disabled_native', [False, True], indirect=True)
async def test_saml_jit_and_repeat_login_need_no_gateway(
    disabled_native: DisabledNative, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.auth import saml_config
    from storage.native_auth import ExternalIdentity

    native = disabled_native
    monkeypatch.setattr(
        saml_config,
        'get_saml_settings',
        lambda: ConfiguredSamlIdentity(connection_id='company', issuer='urn:test:idp'),
    )
    claims: FederatedClaims = {
        'connection_id': 'company',
        'issuer': 'urn:test:idp',
        'subject': 'persistent-person',
        'email': 'sso@example.test',
        'allow_jit': True,
    }
    first = await native.service.complete_federated_login(**claims)
    repeated = await native.service.complete_federated_login(**claims)
    account_id = first.principal.account_id
    assert repeated.principal.account_id == account_id
    assert (
        present(await native.service.authenticate_session(repeated.token))
    ).auth_method == 'saml'
    async with native.sessions() as session:
        account = await session.get(AuthAccount, account_id)
        assert present(account).provisioning_status == 'complete'
        assert (present(await session.get(User, account_id))).role_id is None
        assert await session.get(PasswordCredential, account_id) is None
        memberships = (
            await session.scalars(
                select(OrgMember).where(OrgMember.user_id == account_id)
            )
        ).all()
        assert memberships
        assert all(member.status == 'active' for member in memberships)
        assert all(member.native_provisioning_id is None for member in memberships)
        assert all(not member.llm_api_key.get_secret_value() for member in memberships)
        assert (
            await session.scalar(select(func.count()).select_from(ExternalIdentity))
            == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(NativeExternalWork))
            == 0
        )
    native.http.assert_not_called()


async def test_native_provider_settings_round_trip_without_provisioning(
    disabled_native: DisabledNative,
) -> None:
    native = disabled_native
    person = await enroll(native.service, native.admin_id)
    store = SaasSettingsStore(str(person.principal.account_id))
    settings = await store.load()
    assert present(settings).agent_settings.llm.model == 'openai/gpt-4o'
    assert not present(settings).agent_settings.llm.api_key
    present(settings).update(
        {
            'agent_settings_diff': {
                'llm': {
                    'model': 'openai/gpt-4o',
                    'api_key': 'provider-test-key',
                }
            }
        }
    )
    await store.store(present(settings))
    saved = await store.load()
    assert present(saved).agent_settings.llm.api_key == SecretStr('provider-test-key')
    async with native.sessions() as session:
        member = await session.get(
            OrgMember, (person.principal.account_id, person.principal.account_id)
        )
        assert (
            present(member).status == 'active'
            and present(member).native_provisioning_id is None
        )
        admin = await session.get(OrgMember, (native.admin_id, native.admin_id))
        assert not present(admin).llm_api_key.get_secret_value()
        assert (
            await session.scalar(select(func.count()).select_from(NativeExternalWork))
            == 0
        )
    native.http.assert_not_called()


@pytest.mark.parametrize('disabled_native', [True], indirect=True)
async def test_native_shared_org_provider_key_propagates_without_provisioning(
    disabled_native: DisabledNative,
) -> None:
    from server.routes.org_models import OrgUpdate
    from server.routes.orgs import update_org_defaults_settings

    native = disabled_native
    person = await enroll(native.service, native.admin_id)
    person_id = person.principal.account_id
    async with native.sessions() as session:
        shared_org_id = (present(await session.get(User, person_id))).current_org_id
        assert (
            shared_org_id
            == (present(await session.get(User, native.admin_id))).current_org_id
        )
        assert shared_org_id not in (person_id, native.admin_id)

    store = SaasSettingsStore(str(native.admin_id), effective_org_id=shared_org_id)
    for provider_key in ('shared-provider-test-key', 'rotated-provider-test-key'):
        await update_org_defaults_settings(
            org_id=shared_org_id,
            settings=OrgUpdate(
                agent_settings_diff={
                    'llm': {
                        'model': 'openai/gpt-4o',
                        'base_url': 'https://provider.example.test/v1',
                        'api_key': provider_key,
                    }
                }
            ),
            user_id=str(native.admin_id),
            settings_store=store,
        )

        for account_id in (native.admin_id, person_id):
            saved = await SaasSettingsStore(
                str(account_id), effective_org_id=shared_org_id
            ).load()
            assert present(saved).agent_settings.llm.model == 'openai/gpt-4o'
            assert (
                present(saved).agent_settings.llm.base_url
                == 'https://provider.example.test/v1'
            )
            assert present(saved).agent_settings.llm.api_key == SecretStr(provider_key)
        async with native.sessions() as session:
            org = await session.get(Org, shared_org_id)
            assert present(present(org).llm_api_key).get_secret_value() == provider_key
            for account_id in (native.admin_id, person_id):
                member = await session.get(
                    OrgMember, {'org_id': shared_org_id, 'user_id': account_id}
                )
                assert present(member).llm_api_key.get_secret_value() == provider_key
                assert present(member).status == 'active'
                assert present(member).native_provisioning_id is None
                personal_member = await session.get(
                    OrgMember, {'org_id': account_id, 'user_id': account_id}
                )
                assert not present(personal_member).llm_api_key.get_secret_value()
            assert (
                await session.scalar(
                    select(func.count()).select_from(NativeExternalWork)
                )
                == 0
            )
    native.http.assert_not_called()


async def test_native_delete_and_same_uuid_reonboarding_are_local(
    disabled_native: DisabledNative,
) -> None:
    from server.services.admin_user_lifecycle_service import AdminUserLifecycleService
    from server.services.native_maintenance_service import run_native_maintenance

    native = disabled_native
    person = await enroll(native.service, native.admin_id)
    account_id = person.principal.account_id
    lifecycle = AdminUserLifecycleService()
    await lifecycle.disable_user(str(account_id), actor_user_id=str(native.admin_id))
    with pytest.raises(NativeAuthError):
        await native.service.login('person@example.test', PASSWORD, client_ip='1')
    await lifecycle.enable_user(str(account_id), actor_user_id=str(native.admin_id))
    assert await native.service.login('person@example.test', PASSWORD, client_ip='1')
    await OrgStore.delete_org_cascade(account_id, requester_user_id=str(account_id))
    assert await native.service.authenticate_session(person.token) is None
    restored = await native.service.login(
        'person@example.test', PASSWORD, client_ip='1'
    )
    assert restored.principal.account_id == account_id
    deleted = await lifecycle.delete_user(
        str(account_id), actor_user_id=str(native.admin_id)
    )
    assert present(deleted).cleanup_warnings == []
    result = await run_native_maintenance()
    assert result['error_count'] == 0
    async with native.sessions() as session:
        account = await session.get(AuthAccount, account_id)
        assert (
            present(account).state == 'deleted'
            and present(account).provisioning_status == 'complete'
        )
        assert await session.get(User, account_id) is None
        assert await session.get(PasswordCredential, account_id) is None
        assert (
            await session.scalar(select(func.count()).select_from(NativeExternalWork))
            == 0
        )
    with pytest.raises(NativeAuthError):
        await native.service.login('person@example.test', PASSWORD, client_ip='1')
    native.http.assert_not_called()


async def test_disabled_gateway_preserves_cleanup_for_reenable(
    disabled_native: DisabledNative, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = disabled_native
    work_id, claim_id = uuid4(), uuid4()
    async with native.sessions() as session, session.begin():
        await lock_native_lifecycle(session)
        member = await session.get(OrgMember, (native.admin_id, native.admin_id))
        present(member).native_provisioning_id = work_id
        present(member).status = 'pending_llm_provisioning'
        present(member).llm_api_key = SecretStr('old-managed-key')
        account = await session.get(AuthAccount, native.admin_id)
        present(account).provisioning_status = 'pending'
        session.add(
            NativeExternalWork(
                id=work_id,
                account_id=native.admin_id,
                org_id=native.admin_id,
                kind='provision',
                status='running',
                claim_id=claim_id,
                lease_until=datetime.now(UTC) + timedelta(minutes=2),
                payload={'member_key': 'old-managed-key'},
            )
        )
        session.add(
            NativeExternalWork(
                account_id=native.admin_id, kind='delete_user', payload={}
            )
        )
    reconciler = NativeProvisioningService(native.sessions)
    assert await reconciler.cleanup() == (0, 0)
    provision = AsyncMock(side_effect=AssertionError('Disabled provisioning called'))
    assert await reconciler.reconcile(provision) == (0, 0)
    async with native.sessions() as session, session.begin():
        await lock_native_lifecycle(session)
        member = await session.get(OrgMember, (native.admin_id, native.admin_id))
        assert not (
            await prepare_managed_member(session, present(member), force=True)
        ).get_secret_value()
        work = await session.get(NativeExternalWork, work_id)
        assert present(work).status == 'cleanup' and present(work).payload == {
            'member_key': 'old-managed-key'
        }
        assert (
            present(work).claim_id == claim_id and present(work).lease_until is not None
        )
        assert (
            present(member).status == 'active'
            and present(member).native_provisioning_id is None
        )
        assert (
            present(await session.get(AuthAccount, native.admin_id))
        ).provisioning_status == 'complete'
    provision.assert_not_awaited()
    native.http.assert_not_called()
    # Re-enabling can remove the exact old remote resource after its lease.
    async with native.sessions() as session, session.begin():
        work = await session.get(NativeExternalWork, work_id)
        present(work).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    monkeypatch.setenv('ENABLE_LITELLM', 'true')
    cleanup = AsyncMock()
    monkeypatch.setattr(
        'server.services.native_litellm_adapter.cleanup_native_resource', cleanup
    )
    assert await reconciler.cleanup() == (2, 0)
    assert cleanup.await_args_list[0].args[-1] == {'member_key': 'old-managed-key'}
    async with native.sessions() as session:
        work = await session.get(NativeExternalWork, work_id)
        assert present(work).status == 'complete' and present(work).payload == {}


async def test_disabled_native_adapter_rejects_direct_http_bypass(
    disabled_native: DisabledNative,
) -> None:
    native = disabled_native
    request = NativeProvisioningRequest(
        native.admin_id,
        native.admin_id,
        'admin@example.test',
        uuid4(),
        SecretStr('key'),
    )
    with pytest.raises(LiteLLMIntegrationDisabled):
        await provision_native_member(request)
    await cleanup_native_resource('delete_user', native.admin_id, None, {})
    native.http.assert_not_called()
