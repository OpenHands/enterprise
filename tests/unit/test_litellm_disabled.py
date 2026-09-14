"""The disabled gateway cannot receive requests, even with stale configuration."""

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar, Unpack
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typing_extensions import TypedDict

from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.utils.litellm_integration import LiteLLMIntegrationDisabled
from openhands.sdk.settings import AgentSettingsConfig
from server.constants import ORG_SETTINGS_VERSION
from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
    ManagedLlmKeyOwnershipProcessor,
    ManagedLlmKeyOwnershipTarget,
    enqueue_managed_llm_key_ownership_tasks,
)
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from server.routes import api_keys, billing, orgs
from server.routes.org_models import (
    OrgAppSettingsUpdate,
    OrgBudgetSettingsUpdate,
    OrgBudgetUserOverrideUpdate,
    OrgUpdate,
)
from server.services.admin_user_lifecycle_service import AdminUserLifecycleService
from server.services.org_budget_service import OrgBudgetService
from server.services.org_conversation_service import OrgConversationService
from server.services.org_member_financial_service import OrgMemberFinancialService
from storage.default_org_service import DefaultOrgBootstrapService
from storage.lite_llm_manager import LiteLlmManager
from storage.litellm_models import LiteLlmTeamResponse
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org import Org
from storage.org_app_settings_store import OrgAppSettingsStore
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.org_service import OrgService
from storage.org_store import OrgStore
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from storage.stored_conversation_cost_event import StoredConversationCostEvent
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore
from tests.unit.gateway_saas_types import HttpxSendOptions

USER_ID = '11111111-1111-1111-1111-111111111111'
ORG_ID = '22222222-2222-2222-2222-222222222222'


@pytest.fixture(autouse=True)
def disabled_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    monkeypatch.setenv('ENABLE_BILLING', 'true')
    monkeypatch.setenv('ENABLE_BYOR_EXPORT', 'true')
    monkeypatch.setattr(
        'storage.lite_llm_manager.LITE_LLM_API_URL', 'https://gateway.invalid'
    )
    monkeypatch.setattr('storage.lite_llm_manager.LITE_LLM_API_KEY', 'stale-master-key')
    attempted: list[str] = []

    async def record_request(
        self: httpx.AsyncClient,
        request: httpx.Request,
        **kwargs: Unpack[HttpxSendOptions],
    ) -> httpx.Response:
        attempted.append(str(request.url))
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, 'send', record_request)
    yield attempted
    # Check after the operation: many legacy callers swallow transport errors.
    assert attempted == []


T = TypeVar('T')


async def _ignore_result(result: Awaitable[T]) -> None:
    await result


@dataclass(frozen=True)
class GatewayCase:
    name: str
    public: Callable[[], Awaitable[None]]
    private: Callable[[httpx.AsyncClient], Awaitable[None]]


class MemberIdentity(TypedDict):
    user_id: str


class AddMemberRequest(TypedDict):
    member: MemberIdentity


class GatewayErrorDetail(TypedDict):
    code: str


GATEWAY_METHODS = [
    GatewayCase(
        'create_team',
        lambda: _ignore_result(
            LiteLlmManager.create_team('Organization', ORG_ID, None)
        ),
        lambda client: _ignore_result(
            LiteLlmManager._create_team(client, 'Organization', ORG_ID, None)
        ),
    ),
    GatewayCase(
        'get_team',
        lambda: _ignore_result(LiteLlmManager.get_team(ORG_ID)),
        lambda client: _ignore_result(LiteLlmManager._get_team(client, ORG_ID)),
    ),
    GatewayCase(
        'update_team',
        lambda: _ignore_result(LiteLlmManager.update_team(ORG_ID, None, None)),
        lambda client: _ignore_result(
            LiteLlmManager._update_team(client, ORG_ID, None, None)
        ),
    ),
    GatewayCase(
        'user_exists',
        lambda: _ignore_result(LiteLlmManager.user_exists(USER_ID)),
        lambda client: _ignore_result(LiteLlmManager._user_exists(client, USER_ID)),
    ),
    GatewayCase(
        'create_user',
        lambda: _ignore_result(LiteLlmManager.create_user(None, USER_ID)),
        lambda client: _ignore_result(
            LiteLlmManager._create_user(client, None, USER_ID)
        ),
    ),
    GatewayCase(
        'get_user',
        lambda: _ignore_result(LiteLlmManager.get_user(USER_ID)),
        lambda client: _ignore_result(LiteLlmManager._get_user(client, USER_ID)),
    ),
    GatewayCase(
        'update_user',
        lambda: _ignore_result(LiteLlmManager.update_user(USER_ID)),
        lambda client: _ignore_result(LiteLlmManager._update_user(client, USER_ID)),
    ),
    GatewayCase(
        'delete_user',
        lambda: _ignore_result(LiteLlmManager.delete_user(USER_ID)),
        lambda client: _ignore_result(LiteLlmManager._delete_user(client, USER_ID)),
    ),
    GatewayCase(
        'delete_team',
        lambda: _ignore_result(LiteLlmManager.delete_team(ORG_ID)),
        lambda client: _ignore_result(LiteLlmManager._delete_team(client, ORG_ID)),
    ),
    GatewayCase(
        'add_user_to_team',
        lambda: _ignore_result(LiteLlmManager.add_user_to_team(USER_ID, ORG_ID, None)),
        lambda client: _ignore_result(
            LiteLlmManager._add_user_to_team(client, USER_ID, ORG_ID, None)
        ),
    ),
    GatewayCase(
        'remove_user_from_team',
        lambda: _ignore_result(LiteLlmManager.remove_user_from_team(USER_ID, ORG_ID)),
        lambda client: _ignore_result(
            LiteLlmManager._remove_user_from_team(client, USER_ID, ORG_ID)
        ),
    ),
    GatewayCase(
        'get_user_team_info',
        lambda: _ignore_result(LiteLlmManager.get_user_team_info(USER_ID, ORG_ID)),
        lambda client: _ignore_result(
            LiteLlmManager._get_user_team_info(client, USER_ID, ORG_ID)
        ),
    ),
    GatewayCase(
        'update_user_in_team',
        lambda: _ignore_result(
            LiteLlmManager.update_user_in_team(USER_ID, ORG_ID, None)
        ),
        lambda client: _ignore_result(
            LiteLlmManager._update_user_in_team(client, USER_ID, ORG_ID, None)
        ),
    ),
    GatewayCase(
        'generate_key',
        lambda: _ignore_result(
            LiteLlmManager.generate_key(USER_ID, ORG_ID, 'alias', None)
        ),
        lambda client: _ignore_result(
            LiteLlmManager._generate_key(client, USER_ID, ORG_ID, 'alias', None)
        ),
    ),
    GatewayCase(
        'get_key_info',
        lambda: _ignore_result(LiteLlmManager.get_key_info(ORG_ID, USER_ID)),
        lambda client: _ignore_result(
            LiteLlmManager._get_key_info(client, ORG_ID, USER_ID)
        ),
    ),
    GatewayCase(
        'verify_existing_key',
        lambda: _ignore_result(
            LiteLlmManager.verify_existing_key('stale-key', USER_ID, ORG_ID)
        ),
        lambda client: _ignore_result(
            LiteLlmManager._verify_existing_key(client, 'stale-key', USER_ID, ORG_ID)
        ),
    ),
    GatewayCase(
        'verify_existing_key_strict',
        lambda: _ignore_result(
            LiteLlmManager.verify_existing_key_strict('stale-key', USER_ID, ORG_ID)
        ),
        lambda client: _ignore_result(
            LiteLlmManager._verify_existing_key_strict(
                client, 'stale-key', USER_ID, ORG_ID
            )
        ),
    ),
    GatewayCase(
        'delete_key',
        lambda: _ignore_result(LiteLlmManager.delete_key('stale-key')),
        lambda client: _ignore_result(LiteLlmManager._delete_key(client, 'stale-key')),
    ),
    GatewayCase(
        'get_user_keys',
        lambda: _ignore_result(LiteLlmManager.get_user_keys(USER_ID)),
        lambda client: _ignore_result(LiteLlmManager._get_user_keys(client, USER_ID)),
    ),
    GatewayCase(
        'delete_key_by_alias',
        lambda: _ignore_result(LiteLlmManager.delete_key_by_alias('alias')),
        lambda client: _ignore_result(
            LiteLlmManager._delete_key_by_alias(client, 'alias')
        ),
    ),
    GatewayCase(
        'delete_key_by_alias_strict',
        lambda: _ignore_result(LiteLlmManager.delete_key_by_alias_strict('alias')),
        lambda client: _ignore_result(
            LiteLlmManager._delete_key_by_alias_strict(client, 'alias')
        ),
    ),
    GatewayCase(
        'update_user_keys',
        lambda: _ignore_result(LiteLlmManager.update_user_keys(USER_ID)),
        lambda client: _ignore_result(
            LiteLlmManager._update_user_keys(client, USER_ID)
        ),
    ),
    GatewayCase(
        'get_team_members_financial_data',
        lambda: _ignore_result(LiteLlmManager.get_team_members_financial_data(ORG_ID)),
        lambda client: _ignore_result(
            LiteLlmManager._get_team_members_financial_data(client, ORG_ID)
        ),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'case', GATEWAY_METHODS, ids=[case.name for case in GATEWAY_METHODS]
)
async def test_public_methods_reject_before_creating_http_client(
    case: GatewayCase,
) -> None:
    with patch('storage.lite_llm_manager.httpx.AsyncClient') as factory:
        with pytest.raises(LiteLLMIntegrationDisabled):
            await case.public()
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'case', GATEWAY_METHODS, ids=[case.name for case in GATEWAY_METHODS]
)
async def test_private_methods_cannot_bypass_guard_with_supplied_client(
    case: GatewayCase,
) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(LiteLLMIntegrationDisabled):
            await case.private(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'call',
    [
        pytest.param(
            lambda: _ignore_result(
                LiteLlmManager.sync_free_model_allowlists(AsyncSession())
            ),
            id='sync_free_model_allowlists',
        ),
        pytest.param(
            lambda: _ignore_result(
                LiteLlmManager.create_entries(ORG_ID, USER_ID, Settings(), True)
            ),
            id='create_entries',
        ),
        pytest.param(
            lambda: _ignore_result(
                LiteLlmManager.migrate_entries(ORG_ID, USER_ID, UserSettings())
            ),
            id='migrate_entries',
        ),
        pytest.param(
            lambda: _ignore_result(
                LiteLlmManager.downgrade_entries(ORG_ID, USER_ID, UserSettings())
            ),
            id='downgrade_entries',
        ),
        pytest.param(
            lambda: _ignore_result(
                LiteLlmManager.update_team_and_users_budget(ORG_ID, 100.0)
            ),
            id='update_team_and_users_budget',
        ),
        pytest.param(
            lambda: _ignore_result(LiteLlmManager.ensure_free_team_models(ORG_ID)),
            id='ensure_free_team_models',
        ),
        pytest.param(
            lambda: _ignore_result(LiteLlmManager.ensure_user_in_org(USER_ID, ORG_ID)),
            id='ensure_user_in_org',
        ),
        pytest.param(
            lambda: _ignore_result(
                LiteLlmManager.verify_key('stale-user-key', USER_ID)
            ),
            id='verify_key',
        ),
    ],
)
async def test_standalone_entrypoints_reject_before_client_creation(
    call: Callable[[], Awaitable[None]],
) -> None:
    with patch('storage.lite_llm_manager.httpx.AsyncClient') as factory:
        with pytest.raises(LiteLLMIntegrationDisabled):
            await call()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_key_verification_needs_no_master_key_to_be_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('storage.lite_llm_manager.LITE_LLM_API_KEY', None)
    with pytest.raises(LiteLLMIntegrationDisabled):
        await LiteLlmManager.verify_key('persisted-user-key', USER_ID)


@pytest.mark.asyncio
async def test_new_user_and_org_persist_without_gateway_or_provider_key(
    async_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('server.constants.OPENHANDS_DEFAULT_LLM_MODEL', None)
    monkeypatch.setattr('server.constants.OPENHANDS_DEFAULT_LLM_API_KEY', None)
    async with async_session_maker() as session:
        session.add(Role(id=1, name='owner', rank=1))
        await session.commit()
    with (
        patch('storage.user_store.a_session_maker', async_session_maker),
        patch('storage.org_store.a_session_maker', async_session_maker),
        patch('storage.role_store.a_session_maker', async_session_maker),
        patch.object(LiteLlmManager, 'create_entries', AsyncMock()) as provision,
    ):
        user = await UserStore.create_user(USER_ID, {'email': 'owner@example.com'})
        assert user is not None
        org = await OrgService.create_org_with_owner(
            'Native org', 'Owner', 'owner@example.com', USER_ID
        )
    provision.assert_not_called()
    async with async_session_maker() as session:
        member = await session.get(
            OrgMember, {'org_id': org.id, 'user_id': UUID(USER_ID)}
        )
        assert member is not None
        assert member.llm_api_key.get_secret_value() == ''
        assert (
            member._llm_api_key
        )  # Encrypted empty value satisfies the NOT NULL column.
        stored = await session.get(Org, org.id)
        assert stored is not None
        settings: AgentSettingsConfig = TypeAdapter(
            AgentSettingsConfig
        ).validate_python(stored.agent_settings)
        assert settings.llm is not None
        assert settings.llm.model == 'openai/gpt-4o'
        assert settings.llm.base_url is None
        assert settings.llm.api_key is None


@pytest.mark.asyncio
async def test_org_credit_and_cleanup_paths_skip_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('storage.org_service.ENABLE_BYOR_EXPORT', True)
    with (
        patch.object(LiteLlmManager, 'get_user_team_info', AsyncMock()) as financial,
        patch.object(LiteLlmManager, 'delete_team', AsyncMock()) as delete_team,
        patch.object(LiteLlmManager, 'delete_user', AsyncMock()) as delete_user,
    ):
        result = await OrgService.get_org_credits(USER_ID, UUID(ORG_ID))
        assert result.available is False
        assert result.credits is None
        assert (
            await OrgService.check_byor_export_enabled(USER_ID, UUID(ORG_ID)) is False
        )
        assert (
            await OrgService._cleanup_litellm_resources(UUID(ORG_ID), USER_ID) is None
        )
        await OrgStore._delete_litellm_user_best_effort(USER_ID, UUID(ORG_ID))
    financial.assert_not_called()
    delete_team.assert_not_called()
    delete_user.assert_not_called()
    assert (
        await DefaultOrgBootstrapService._create_member_litellm_api_key(
            UUID(ORG_ID), UUID(USER_ID)
        )
        == ''
    )


@pytest.mark.asyncio
async def test_user_deletion_continues_identity_cleanup() -> None:
    token_manager = MagicMock()
    token_manager.disable_keycloak_user = AsyncMock()
    token_manager.delete_keycloak_user = AsyncMock(return_value=True)
    service = AdminUserLifecycleService(token_manager)
    user = User(id=UUID(USER_ID), email='member@example.com')
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch.object(service, '_ensure_not_last_active_superadmin', AsyncMock()),
        patch.object(service, '_set_disabled', AsyncMock()),
        patch.object(service, '_delete_api_keys', AsyncMock()),
        patch.object(service, '_delete_offline_token', AsyncMock()),
        patch.object(service, '_delete_user_data', AsyncMock()) as delete_data,
        patch.object(LiteLlmManager, 'delete_user', AsyncMock()) as delete_gateway,
    ):
        result = await service.delete_user(USER_ID)
    assert result is not None
    assert result.cleanup_warnings == []
    delete_data.assert_awaited_once_with(USER_ID)
    token_manager.delete_keycloak_user.assert_awaited_once_with(USER_ID)
    delete_gateway.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'call',
    [
        pytest.param(
            lambda: _ignore_result(
                billing.get_credits(user_id=USER_ID, effective_org_id=UUID(ORG_ID))
            ),
            id='billing.get_credits',
        ),
        pytest.param(
            lambda: _ignore_result(
                billing.success_callback(
                    session_id='old-checkout', request=Request({'type': 'http'})
                )
            ),
            id='billing.success_callback',
        ),
        pytest.param(
            lambda: _ignore_result(billing.validate_billing_enabled()),
            id='billing.validate_billing_enabled',
        ),
        pytest.param(
            lambda: _ignore_result(
                api_keys.refresh_managed_llm_api_key(
                    user_id=USER_ID, effective_org_id=UUID(ORG_ID)
                )
            ),
            id='api_keys.refresh_managed_llm_api_key',
        ),
        pytest.param(
            lambda: _ignore_result(
                api_keys.get_llm_api_key_for_byor(
                    user_id=USER_ID, effective_org_id=UUID(ORG_ID)
                )
            ),
            id='api_keys.get_llm_api_key_for_byor',
        ),
        pytest.param(
            lambda: _ignore_result(
                api_keys.refresh_llm_api_key_for_byor(
                    user_id=USER_ID, effective_org_id=UUID(ORG_ID)
                )
            ),
            id='api_keys.refresh_llm_api_key_for_byor',
        ),
        pytest.param(
            lambda: _ignore_result(
                orgs.get_org_members_financial(org_id=UUID(ORG_ID), user_id=USER_ID)
            ),
            id='orgs.get_org_members_financial',
        ),
        pytest.param(
            lambda: _ignore_result(
                orgs.get_org_budget_settings(org_id=UUID(ORG_ID), user_id=USER_ID)
            ),
            id='orgs.get_org_budget_settings',
        ),
        pytest.param(
            lambda: _ignore_result(
                orgs.update_org_budget_settings(
                    org_id=UUID(ORG_ID),
                    update=OrgBudgetSettingsUpdate(),
                    response=Response(),
                    user_id=USER_ID,
                )
            ),
            id='orgs.update_org_budget_settings',
        ),
        pytest.param(
            lambda: _ignore_result(
                orgs.upsert_org_budget_override(
                    org_id=UUID(ORG_ID),
                    user_id=USER_ID,
                    update=OrgBudgetUserOverrideUpdate(is_disabled=True),
                    response=Response(),
                )
            ),
            id='orgs.upsert_org_budget_override',
        ),
        pytest.param(
            lambda: _ignore_result(
                orgs.delete_org_budget_override(
                    org_id=UUID(ORG_ID), user_id=USER_ID, response=Response()
                )
            ),
            id='orgs.delete_org_budget_override',
        ),
    ],
)
async def test_gateway_feature_handlers_are_explicitly_unavailable(
    call: Callable[[], Awaitable[None]],
) -> None:
    with pytest.raises(HTTPException) as exc:
        await call()
    assert exc.value.status_code == 503
    assert (
        TypeAdapter(GatewayErrorDetail).validate_python(exc.value.detail)['code']
        == 'litellm_disabled'
    )


@pytest.mark.asyncio
async def test_budget_services_reject_before_reading_or_mutating_store() -> None:
    store = MagicMock()
    service = OrgBudgetService(store=store)
    calls = [
        service.get_budget_state(UUID(ORG_ID)),
        service.update_budget_settings(UUID(ORG_ID), OrgBudgetSettingsUpdate()),
        service.upsert_user_override(UUID(ORG_ID), UUID(USER_ID), None, False),
        service.delete_user_override(UUID(ORG_ID), UUID(USER_ID)),
        service.get_user_budget_row(UUID(ORG_ID), UUID(USER_ID)),
        OrgMemberFinancialService.get_org_members_financial_data(UUID(ORG_ID)),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            await call
        assert (
            TypeAdapter(GatewayErrorDetail).validate_python(exc.value.detail)['code']
            == 'litellm_disabled'
        )
    assert store.mock_calls == []


@pytest.mark.asyncio
async def test_pending_gateway_tasks_skip_without_altering_reconciliation(
    async_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    import run_budget_maintenance

    org_id = uuid4()
    baseline_at = datetime(2025, 1, 1, tzinfo=UTC)
    async with async_session_maker() as session:
        session.add(Org(id=org_id, name='Budget org'))
        session.add(Role(id=1, name='owner', rank=1))
        session.add(User(id=UUID(USER_ID), current_org_id=org_id))
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=UUID(USER_ID),
                role_id=1,
                llm_api_key='stale-managed-key',
                managed_llm_key_ownership_version=0,
            )
        )
        session.add(
            OrgBudgetSettings(
                org_id=org_id,
                enabled=True,
                reset_day=1,
                monthly_limit=50.0,
                cycle_start_at=baseline_at,
                cycle_start_spend=42.0,
            )
        )
        await session.commit()
    task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
    budget = OrgBudgetMaintenanceProcessor(org_ids=[str(org_id)])
    keys = ManagedLlmKeyOwnershipProcessor(
        targets=[ManagedLlmKeyOwnershipTarget(org_id=str(org_id), user_id=USER_ID)]
    )
    assert (await budget(task))['skipped'] == 'litellm_disabled'
    assert (await keys(task))['skipped'] == 'litellm_disabled'
    assert enqueue_managed_llm_key_ownership_tasks() == 0
    assert run_budget_maintenance.enqueue_budget_tasks() == 0
    async with async_session_maker() as session:
        result = await session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.cycle_start_at == baseline_at
        assert row.cycle_start_spend == 42.0
        assert row.litellm_last_sync_status is None
        member = await session.get(
            OrgMember, {'org_id': org_id, 'user_id': UUID(USER_ID)}
        )
        assert member is not None
        assert member.managed_llm_key_ownership_version == 0
        assert member is not None
        assert member.llm_api_key.get_secret_value() == 'stale-managed-key'


@pytest.mark.asyncio
async def test_generic_maintenance_continues_without_gateway_jobs() -> None:
    import run_maintenance_tasks

    with (
        patch('server.auth.bootstrap.verify_auth_installation', AsyncMock()),
        patch.object(run_maintenance_tasks, 'set_stale_task_error') as stale,
        patch.object(
            run_maintenance_tasks, 'run_tasks', AsyncMock(return_value=0)
        ) as run,
    ):
        await run_maintenance_tasks.main()
    stale.assert_called_once()
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_reenable_provisions_missing_gateway_membership_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    profiles = {
        'profiles': {'native': {'model': 'openai/gpt-4o', 'api_key': 'byok-key'}}
    }
    async with async_session_maker() as session:
        session.add(Role(id=1, name='owner', rank=1))
        session.add(
            Org(
                id=UUID(ORG_ID),
                name='Native org',
                org_version=ORG_SETTINGS_VERSION,
                agent_settings={'llm': {'model': 'openai/gpt-4o', 'base_url': None}},
                llm_profiles=profiles,
            )
        )
        session.add(User(id=UUID(USER_ID), current_org_id=UUID(ORG_ID)))
        await session.flush()
        session.add(
            OrgMember(
                org_id=UUID(ORG_ID),
                user_id=UUID(USER_ID),
                role_id=1,
                llm_api_key='old-native-provider-key',
            )
        )
        await session.commit()
    monkeypatch.setenv('ENABLE_LITELLM', 'true')
    monkeypatch.setenv('LITE_LLM_API_URL', 'https://gateway.invalid')
    requests: list[tuple[str, str]] = []
    authorization_headers: list[str | None] = []
    team: LiteLlmTeamResponse | None = None
    user_exists = False

    async def gateway(
        self: httpx.AsyncClient,
        request: httpx.Request,
        **kwargs: Unpack[HttpxSendOptions],
    ) -> httpx.Response:
        nonlocal team, user_exists

        requests.append((request.method, request.url.path))
        authorization_headers.append(request.headers.get('authorization'))
        if request.url.path == '/team/info':
            return httpx.Response(
                200 if team else 404, json=team or {}, request=request
            )
        if request.url.path == '/team/new':
            team = {
                'team_info': {'max_budget': None, 'models': []},
                'team_memberships': [],
            }
        elif request.url.path == '/user/info':
            if team is None:
                # An ownership lookup on the previous native key must not
                # trigger permissive verification during a transient failure.
                return httpx.Response(503, json={}, request=request)
            return httpx.Response(
                200 if user_exists else 404,
                json={'user_info': {'user_id': USER_ID}} if user_exists else {},
                request=request,
            )
        elif request.url.path == '/user/new':
            user_exists = True
        elif request.url.path == '/team/member_add':
            assert team is not None
            members = team['team_memberships']
            assert members is not None
            body = TypeAdapter(AddMemberRequest).validate_json(request.content)
            members.append({'user_id': body['member']['user_id']})
        elif request.url.path == '/key/delete':
            pass
        elif request.url.path == '/key/generate':
            return httpx.Response(200, json={'key': 'new-managed-key'}, request=request)
        elif request.url.path == '/v1/models':
            return httpx.Response(200, json={}, request=request)
        else:
            pytest.fail(
                f'Unexpected gateway request: {request.method} {request.url.path}'
            )
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, 'send', gateway)
    with (
        patch(
            'storage.lite_llm_manager._is_billing_enabled',
            AsyncMock(return_value=False),
        ),
        patch('storage.org_store.a_session_maker', async_session_maker),
    ):
        await OrgStore.update_org(
            UUID(ORG_ID),
            OrgUpdate(
                agent_settings_diff={
                    'llm': {
                        'model': 'openhands/claude-sonnet-4',
                        'base_url': 'https://gateway.invalid',
                    }
                }
            ),
            USER_ID,
        )
        first_writes = [item for item in requests if item[0] == 'POST']
        requests.clear()
        await LiteLlmManager.ensure_user_in_org(USER_ID, ORG_ID)
    assert first_writes == [
        ('POST', '/team/new'),
        ('POST', '/user/new'),
        ('POST', '/team/member_add'),
        ('POST', '/key/delete'),
        ('POST', '/key/generate'),
    ]
    assert all(method == 'GET' for method, _ in requests)
    assert 'Bearer old-native-provider-key' not in authorization_headers
    async with async_session_maker() as session:
        org = await session.get(Org, UUID(ORG_ID))
        member = await session.get(
            OrgMember, {'org_id': UUID(ORG_ID), 'user_id': UUID(USER_ID)}
        )
        assert org is not None
        assert org.org_version == ORG_SETTINGS_VERSION
        assert org is not None
        assert org.llm_profiles == profiles
        assert member is not None
        assert member.llm_api_key.get_secret_value() == 'new-managed-key'


@pytest.mark.asyncio
@pytest.mark.parametrize('app_settings', [False, True])
async def test_org_provider_change_clears_incompatible_keys(
    async_session_maker: async_sessionmaker[AsyncSession], app_settings: bool
) -> None:
    org_id, owner_id, byok_id = uuid4(), uuid4(), uuid4()
    async with async_session_maker() as session:
        session.add(Role(id=1, name='owner', rank=1))
        session.add(
            Org(
                id=org_id,
                name='Native defaults',
                llm_api_key='old-org-openai-key',
                agent_settings={'llm': {'model': 'openai/gpt-4o', 'base_url': None}},
            )
        )
        session.add_all(
            [
                User(id=owner_id, current_org_id=org_id),
                User(id=byok_id, current_org_id=org_id),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrgMember(
                    org_id=org_id,
                    user_id=owner_id,
                    role_id=1,
                    llm_api_key='old-personal-openai-key',
                    has_custom_llm_api_key=True,
                ),
                OrgMember(
                    org_id=org_id,
                    user_id=byok_id,
                    role_id=1,
                    llm_api_key='explicit-byok-openai-key',
                    has_custom_llm_api_key=True,
                    agent_settings_diff={
                        'llm': {
                            'model': 'openai/gpt-4o',
                            'base_url': 'https://api.openai.com/v1',
                        }
                    },
                ),
            ]
        )
        await session.commit()
    diff = {'llm': {'model': 'anthropic/claude-sonnet-4', 'base_url': None}}
    if app_settings:
        async with async_session_maker() as session:
            await OrgAppSettingsStore(session).update_org_app_settings(
                org_id, OrgAppSettingsUpdate(agent_settings_diff=diff)
            )
            await session.commit()
    else:
        with patch('storage.org_store.a_session_maker', async_session_maker):
            await OrgStore.update_org(
                org_id, OrgUpdate(agent_settings_diff=diff), str(owner_id)
            )
    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        owner = await session.get(OrgMember, {'org_id': org_id, 'user_id': owner_id})
        byok = await session.get(OrgMember, {'org_id': org_id, 'user_id': byok_id})
        assert org is not None
        assert org.llm_api_key is None
        assert owner is not None
        assert owner.llm_api_key.get_secret_value() == ''
        assert owner is not None
        assert owner.has_custom_llm_api_key is False
        # App settings leave explicit member overrides intact; org defaults
        # intentionally apply the administrator's LLM selection to every member.
        assert byok is not None
        assert byok.llm_api_key.get_secret_value() == (
            'explicit-byok-openai-key' if app_settings else ''
        )
        assert byok is not None
        assert byok.has_custom_llm_api_key is app_settings


@pytest.mark.asyncio
async def test_stale_org_version_rename_and_delete_remain_local(
    async_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    org_id = uuid4()
    stale = {
        'llm': {
            'model': 'openhands/claude-sonnet-4',
            'base_url': 'https://gateway.invalid',
        }
    }
    async with async_session_maker() as session:
        session.add(Org(id=org_id, name='Old org', org_version=0, agent_settings=stale))
        await session.commit()
    with patch('storage.org_store.a_session_maker', async_session_maker):
        org = await OrgStore.get_org_by_id(org_id)
        assert org is not None
        assert org.agent_settings == stale
        renamed = await OrgStore.update_org(org_id, OrgUpdate(name='Renamed org'))
        assert renamed is not None
        assert renamed.name == 'Renamed org'
        assert await OrgStore.delete_org_cascade(org_id) is not None
    async with async_session_maker() as session:
        assert await session.get(Org, org_id) is None


@pytest.mark.asyncio
async def test_local_usage_keeps_sql_spend_and_omits_gateway_budget_overlay(
    async_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    org_id, user_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        session.add(Org(id=org_id, name='Usage org'))
        session.add(User(id=user_id, current_org_id=org_id, email='usage@example.com'))
        await session.flush()
        session.add(
            StoredConversationMetadata(
                conversation_id='native-usage',
                conversation_version='V1',
                llm_model='openai/gpt-4o',
                accumulated_cost=3.5,
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            StoredConversationMetadataSaas(
                conversation_id='native-usage', user_id=user_id, org_id=org_id
            )
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='native-usage', cost_delta=3.5, occurred_at=now
            )
        )
        session.add(
            OrgBudgetSettings(
                org_id=org_id,
                enabled=True,
                default_user_monthly_limit=10.0,
                cycle_start_at=now,
            )
        )
        session.add(
            OrgUserBudgetOverride(org_id=org_id, user_id=user_id, is_disabled=True)
        )
        await session.commit()
    async with async_session_maker() as session:
        usage = await OrgConversationService(session).get_user_usage_stats(org_id)
    assert len(usage.items) == 1
    assert usage.items[0].spend_lifetime == 3.5
    assert usage.items[0].spend_mtd == 3.5
    assert usage.items[0].budget_monthly_limit is None
    assert usage.items[0].budget_is_disabled is False


@pytest.mark.asyncio
async def test_reenable_team_creation_race_preserves_existing_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'true')
    attempts: list[tuple[str, str]] = []
    lookups = 0

    async def gateway(
        self: httpx.AsyncClient,
        request: httpx.Request,
        **kwargs: Unpack[HttpxSendOptions],
    ) -> httpx.Response:
        nonlocal lookups
        attempts.append((request.method, request.url.path))
        if request.url.path == '/team/info':
            lookups += 1
            if lookups == 1:
                return httpx.Response(404, json={}, request=request)
            return httpx.Response(
                200,
                json={
                    'team_info': {'max_budget': 42.0, 'models': ['custom-model']},
                    'team_memberships': [{'user_id': USER_ID}],
                },
                request=request,
            )
        if request.url.path == '/team/new':
            return httpx.Response(
                400,
                text='Team already exists. Please use a different team id',
                request=request,
            )
        if request.url.path == '/user/info':
            return httpx.Response(
                200, json={'user_info': {'user_id': USER_ID}}, request=request
            )
        return httpx.Response(500, request=request)

    monkeypatch.setattr(httpx.AsyncClient, 'send', gateway)
    with (
        patch(
            'storage.lite_llm_manager._is_billing_enabled',
            AsyncMock(return_value=False),
        ),
        patch.object(
            LiteLlmManager, '_team_alias_for_org', AsyncMock(return_value='Org')
        ),
    ):
        await LiteLlmManager.ensure_user_in_org(USER_ID, ORG_ID)
    assert [attempt for attempt in attempts if attempt[0] == 'POST'] == [
        ('POST', '/team/new')
    ]
