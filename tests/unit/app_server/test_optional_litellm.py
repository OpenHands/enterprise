"""The disabled gateway is never discovered, inherited, or dispatched."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationInfoPage,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.settings.llm_profiles import LLMProfiles, resolve_profile_llm
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.utils.litellm_integration import (
    LiteLLMIntegrationDisabled,
    is_litellm_enabled,
    is_managed_llm,
    validate_agent_llm_payload,
    validate_llm_configuration,
)
from openhands.sdk.llm import LLM
from openhands.sdk.settings import OpenHandsAgentSettings
from tests.unit.app_server.service_mock_fixtures import make_conversation_service


@pytest.fixture
def disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    for name in (
        'LLM_MODEL',
        'LLM_BASE_URL',
        'LLM_API_KEY',
        'OPENHANDS_DEFAULT_LLM_MODEL',
        'OPENHANDS_DEFAULT_LLM_BASE_URL',
        'OPENHANDS_DEFAULT_LLM_API_KEY',
        'LITELLM_KNOWN_GATEWAY_URLS',
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (None, True),
        ('true', True),
        ('TrUe', True),
        ('FaLsE', False),
        ('1', True),
        ('false', False),
        ('0', False),
    ],
)
def test_flag(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv('ENABLE_LITELLM', raising=False)
    else:
        monkeypatch.setenv('ENABLE_LITELLM', value)
    assert is_litellm_enabled() is expected


@pytest.mark.parametrize(
    ('model', 'url'),
    [
        ('openhands/old-alias', None),
        ('litellm_proxy/old-alias', 'https://api.openai.com/v1'),
        ('openai/old-alias', 'https://llm-proxy.app.all-hands.dev/v1/'),
        ('openai/old-alias', 'https://GATEWAY.EXAMPLE:443/v1'),
    ],
)
def test_managed_transport_cannot_hide_behind_native_prefix(
    disabled: None, monkeypatch: pytest.MonkeyPatch, model: str, url: str | None
) -> None:
    monkeypatch.setenv('LITELLM_KNOWN_GATEWAY_URLS', '["https://gateway.example"]')
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_llm_configuration(model, url)


def test_native_defaults_remain_keyless_without_custom_endpoint(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings()
    assert not is_managed_llm(
        settings.agent_settings.llm.model, settings.agent_settings.llm.base_url
    )
    assert settings.agent_settings.llm.api_key is None
    assert settings.agent_settings.llm.base_url is None
    assert settings.llm_profiles.active is None
    monkeypatch.setenv('OPENHANDS_DEFAULT_LLM_MODEL', 'anthropic/claude-sonnet-4-5')
    monkeypatch.setenv('OPENHANDS_DEFAULT_LLM_API_KEY', 'native-key')
    configured = Settings().agent_settings.llm
    assert configured.model == 'anthropic/claude-sonnet-4-5'
    assert configured.base_url is None
    assert configured.api_key == SecretStr('native-key')


def test_malformed_known_origins_fail_closed(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('LITELLM_KNOWN_GATEWAY_URLS', 'not-json')
    with pytest.raises(ValueError, match='JSON list'):
        validate_llm_configuration('openai/old-alias', 'https://old-gateway.example/v1')


@pytest.mark.asyncio
async def test_proxy_injector_bypasses_warm_cache_and_egress(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.services.injector import InjectorState
    from openhands.app_server.utils.llm import ModelsResponse
    from server.verified_models.litellm_proxy_model_router import (
        LiteLLMProxyModelService,
        LiteLLMProxyModelServiceInjector,
    )

    record = AsyncMock(return_value=httpx.Response(200, json={'data': []}))

    monkeypatch.setattr(httpx.AsyncClient, 'get', record)
    monkeypatch.setattr(
        LiteLLMProxyModelService,
        '_shared_response',
        ModelsResponse(
            models=['openhands/managed'],
            verified_models=['managed'],
            verified_providers=['openhands'],
            hidden_models=['openhands/old'],
            default_model='openhands/managed',
            free_models=['openhands/managed'],
        ),
    )
    monkeypatch.setattr(LiteLLMProxyModelService, '_shared_fetched_at', float('inf'))
    injector = LiteLLMProxyModelServiceInjector()
    async for service in injector.inject(InjectorState()):
        assert isinstance(service, LiteLLMProxyModelService)
        result = await service._get_models_response()
        assert result.models
        assert all(not is_managed_llm(model) for model in result.models)
        assert result.default_model is None
        assert result.hidden_models == []
        assert result.free_models == []
        assert 'openhands' not in result.verified_providers
        verified = await service.search_llm_models(verified_eq=True)
        assert verified.items
        assert all(
            item.provider not in ('openhands', 'litellm_proxy')
            for item in verified.items
        )
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_flag_overrides_structured_and_database_flags(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server import config
    from openhands.app_server.web_client import (
        default_web_client_config_injector as module,
    )
    from openhands.app_server.web_client.web_client_models import WebClientFeatureFlags

    monkeypatch.setattr(
        config, 'get_global_config', lambda: SimpleNamespace(app_mode='saas')
    )
    monkeypatch.setattr(
        module,
        '_get_db_feature_flags',
        AsyncMock(return_value={'ENABLE_BILLING': True, 'ENABLE_LITELLM': True}),
    )
    resolve = AsyncMock(return_value=True)
    monkeypatch.setattr(module, '_resolve_flag', resolve)
    injector = module.DefaultWebClientConfigInjector(
        feature_flags=WebClientFeatureFlags(
            enable_litellm=True,
            enable_billing=True,
            enable_byor_export=True,
        )
    )
    config_result = await injector.get_web_client_config()
    assert config_result.feature_flags.enable_litellm is False
    assert config_result.feature_flags.enable_billing is False
    assert config_result.feature_flags.enable_byor_export is False
    assert config_result.db_feature_flags['ENABLE_LITELLM'] is False
    assert config_result.db_feature_flags['ENABLE_BILLING'] is False
    resolve.assert_not_awaited()


def test_repairing_managed_settings_does_not_inherit_gateway_key(
    disabled: None,
) -> None:
    settings = Settings(
        agent_settings={'llm': {'model': 'openhands/old', 'api_key': 'managed-key'}}
    )
    settings.update(
        {'agent_settings_diff': {'llm': {'model': 'openai/gpt-4o', 'base_url': None}}}
    )
    assert settings.agent_settings.llm.api_key is None


def test_profile_fallback_requires_compatible_provider(disabled: None) -> None:
    current = LLM(model='openai/gpt-4o', api_key='private')
    other = LLM(model='anthropic/claude-sonnet-4-5')
    resolved = resolve_profile_llm(
        other,
        managed_proxy_url='https://gateway.example',
        fallback_api_key=current.api_key,
        fallback_llm=current,
    )
    assert resolved.api_key is None
    same = resolve_profile_llm(
        LLM(model='openai/gpt-4o-mini'),
        managed_proxy_url='https://gateway.example',
        fallback_api_key=current.api_key,
        fallback_llm=current,
    )
    assert same.api_key == SecretStr('private')


@pytest.mark.parametrize('nested', ['condenser', 'planning_llm'])
def test_secondary_llms_are_guarded(disabled: None, nested: str) -> None:
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_agent_llm_payload(
            {
                'llm': {'model': 'openai/gpt-4o'},
                nested: {'llm': {'model': 'openhands/old'}},
            }
        )


@pytest.mark.asyncio
async def test_default_profile_does_not_query_or_materialize_gateway(
    disabled: None,
) -> None:
    from server.verified_models.default_profile import (
        get_openhands_default_model_name,
        materialize_default_llm_profile,
    )

    session = AsyncMock()
    assert await get_openhands_default_model_name(session) is None
    session.execute.assert_not_awaited()
    profiles = LLMProfiles(
        profiles={
            'Default': LLM(model='openhands/old'),
            'Native': LLM(model='openai/gpt-4o'),
        },
        active='Default',
    )
    materialize_default_llm_profile(profiles, 'managed-default')
    assert list(profiles.profiles) == ['Native']
    assert profiles.active is None
    native = LLMProfiles(
        profiles={'Default': LLM(model='openai/gpt-4o')}, active='Default'
    )
    assert (
        materialize_default_llm_profile(native, 'managed-default').active == 'Default'
    )


@pytest.mark.asyncio
async def test_historical_settings_can_be_read_and_unrelated_fields_saved(
    disabled: None,
) -> None:
    from openhands.app_server.settings.settings_router import store_settings

    old = Settings(
        agent_settings={'llm': {'model': 'openhands/old', 'api_key': 'managed-key'}}
    )
    store = AsyncMock()
    store.load.return_value = old
    response = await store_settings(
        {'language': 'en'}, settings_store=store, user_id=None
    )
    assert response.status_code == 200
    assert store.store.call_args.args[0].agent_settings.llm.model == 'openhands/old'
    store.store.reset_mock()
    response = await store_settings(
        {'agent_settings_diff': {'llm': {'model': 'openhands/new'}}},
        settings_store=store,
        user_id=None,
    )
    assert response.status_code in (400, 422)
    store.store.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('expose', [False, True])
@pytest.mark.parametrize(
    ('model', 'url'),
    [('openhands/old', None), ('openai/old-alias', 'https://retired.example/v1')],
)
async def test_sdk_projection_removes_managed_main_key_and_profiles(
    disabled: None,
    monkeypatch: pytest.MonkeyPatch,
    expose: bool,
    model: str,
    url: str | None,
) -> None:
    from server.routes import users_v1

    monkeypatch.setenv('LITELLM_KNOWN_GATEWAY_URLS', '["https://retired.example"]')
    user = UserInfo(
        id='user',
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(model=model, base_url=url, api_key='managed-secret')
        ),
        llm_profiles=LLMProfiles(
            profiles={
                'Old': LLM(model=model, base_url=url, api_key='managed-secret'),
                'Direct': LLM(model='openai/gpt-4o', api_key='direct-secret'),
            }
        ),
    )
    context = AsyncMock()
    context.get_user_info.return_value = user
    monkeypatch.setattr(
        users_v1, '_get_org_info_from_context', AsyncMock(return_value=None)
    )
    auth = AsyncMock()
    monkeypatch.setattr(users_v1, 'validate_session_key_ownership', auth)
    response = await users_v1.get_current_user_saas(
        context, expose_secrets=expose, x_session_api_key='session'
    )
    content = json.loads(response.body)
    assert not is_managed_llm(content['llm_model'], content['llm_base_url'])
    assert content['agent_settings']['llm']['api_key'] is None
    assert 'managed-secret' not in bytes(response.body).decode()
    assert 'Old' not in content['llm_profiles']['profiles']
    assert content['llm_profiles']['profiles']['Direct']['api_key'] == (
        'direct-secret' if expose else '**********'
    )
    assert user.agent_settings.llm.model == model
    if expose:
        auth.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('managed_target', ['override', 'title', 'active_title'])
async def test_start_checks_effective_models_before_sandbox(
    disabled: None, managed_target: str
) -> None:
    from openhands.app_server.app_conversation.app_conversation_models import (
        AppConversationStartRequest,
    )
    from openhands.app_server.app_conversation.live_status_app_conversation_service import (
        _resolve_title_llm_profile,
    )

    user = UserInfo(
        id='user',
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(model='openai/gpt-4o', api_key='native')
        ),
    )
    request = AppConversationStartRequest()
    if managed_target == 'override':
        request.llm_model = 'openhands/old'
    else:
        user.llm_profiles.save(
            'Old',
            LLM(
                model='openai/gpt-4o', base_url='https://llm-proxy.app.all-hands.dev/v1'
            ),
        )
        if managed_target == 'title':
            user.title_llm_profile = 'Old'
        else:
            user.llm_profiles.active = 'Old'
    context = AsyncMock()
    context.get_user_id.return_value = 'user'
    context.get_user_email.return_value = None
    context.get_user_info.return_value = user
    service = make_conversation_service(user_context=context)
    tasks = service._start_app_conversation(request)
    if managed_target == 'active_title':
        # An unusable implicit profile is omitted: the agent server will title
        # with the effective direct LLM. Explicit choices still fail closed.
        assert _resolve_title_llm_profile(user) is None
        task = await anext(tasks)
        assert task.request is request
        await tasks.aclose()
    else:
        with pytest.raises(LiteLLMIntegrationDisabled):
            async for _ in tasks:
                pass
    service.sandbox_service.start_sandbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_prunes_managed_profiles_without_dispatching_them(
    disabled: None,
) -> None:
    user = UserInfo(
        id='user',
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(model='openai/gpt-4o', api_key='native')
        ),
        llm_profiles=LLMProfiles(
            profiles={
                'Old': LLM(model='openhands/old'),
                'Direct': LLM(model='openai/gpt-4o'),
            }
        ),
    )
    requests: list[httpx.Request] = []

    async def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(
                200, json={'profiles': [{'name': 'Old'}, {'name': 'Direct'}]}
            )
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
        context = AsyncMock()
        context.get_user_info.return_value = user
        service = make_conversation_service(user_context=context, httpx_client=client)
        await service._seed_sandbox_profiles('https://sandbox.example', 'session')

    posts = [
        json.loads(request.content) for request in requests if request.method == 'POST'
    ]
    assert len(posts) == 1
    assert posts[0]['llm']['model'] == 'openai/gpt-4o'
    assert posts[0]['llm']['api_key'] == 'native'
    assert any(
        request.method == 'DELETE' and request.url.path.endswith('/Old')
        for request in requests
    )
    assert all(request.url.host == 'sandbox.example' for request in requests)


@pytest.mark.asyncio
async def test_resume_requires_provenance_and_invalidates_on_reenable(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server import config
    from openhands.app_server.sandbox.sandbox_service import SandboxService

    conversation = AppConversationInfo(
        created_by_user_id=None,
        sandbox_id='sandbox',
        tags={'direct_llm_validated': '1'},
    )
    service = AsyncMock()
    service.search_app_conversation_info.return_value = AppConversationInfoPage(
        items=[conversation]
    )
    sandbox = MagicMock(spec=SandboxService)

    @asynccontextmanager
    async def inject(_: InjectorState) -> AsyncIterator[AsyncMock]:
        yield service

    monkeypatch.setattr(config, 'get_app_conversation_info_service', inject)
    await SandboxService.validate_resume_configuration(sandbox, 'sandbox')
    monkeypatch.setenv('ENABLE_LITELLM', 'true')
    await SandboxService.validate_resume_configuration(sandbox, 'sandbox')
    assert conversation.tags == {}
    service.save_app_conversation_info.assert_awaited_once()
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    with pytest.raises(LiteLLMIntegrationDisabled, match='older sandbox'):
        await SandboxService.validate_resume_configuration(sandbox, 'sandbox')


def test_enabled_gateway_credentials_do_not_reach_custom_openhands_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.app_server.utils.litellm_integration import (
        llm_credentials_compatible,
    )

    monkeypatch.setenv('ENABLE_LITELLM', 'true')
    managed = LLM(model='openhands/gpt-4o', api_key='managed-key')
    external = LLM(model='openhands/gpt-4o', base_url='https://external.example/v1')
    assert not llm_credentials_compatible(
        managed.model, managed.base_url, external.model, external.base_url
    )
    resolved = resolve_profile_llm(
        external,
        managed_proxy_url='https://llm-proxy.app.all-hands.dev',
        fallback_api_key=managed.api_key,
        fallback_llm=managed,
    )
    assert resolved.api_key is None


@pytest.mark.asyncio
async def test_disabled_base_user_projection_accepts_acp_settings(
    disabled: None,
) -> None:
    from openhands.app_server.user.user_router import get_current_user
    from openhands.sdk.settings import ACPAgentSettings

    user = UserInfo(
        id='user', agent_settings=ACPAgentSettings(acp_server='claude-code')
    )
    context = AsyncMock()
    context.get_user_info.return_value = user
    result = await get_current_user(context, expose_secrets=False)
    assert isinstance(result, UserInfo)
    assert result.agent_settings.agent_kind == 'acp'


def test_switching_agent_kind_does_not_require_previous_llm(disabled: None) -> None:
    from openhands.sdk.settings import ACPAgentSettings

    settings = Settings(agent_settings=ACPAgentSettings(acp_server='claude-code'))
    settings.update(
        {
            'agent_settings_diff': {
                'agent_kind': 'openhands',
                'llm': {'model': 'openai/gpt-4o'},
            }
        }
    )
    assert settings.agent_settings.llm.model == 'openai/gpt-4o'


def test_profile_summaries_identify_former_gateway(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('LITELLM_KNOWN_GATEWAY_URLS', '["https://former.example"]')
    profiles = LLMProfiles(
        profiles={
            'Old': LLM(model='openai/gpt-4o', base_url='https://FORMER.example:443/v1')
        }
    )
    assert profiles.summaries()[0]['requires_litellm'] is True


@pytest.mark.asyncio
async def test_org_profile_summary_classifies_connection_endpoint(
    disabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.settings.provider_connections import ProviderConnections
    from server.routes import org_profiles

    monkeypatch.setenv('LITELLM_KNOWN_GATEWAY_URLS', '["https://former.example"]')
    profiles = LLMProfiles(
        profiles={'Linked': LLM(model='openai/gpt-4o', provider_connection_id='old')}
    )
    connections = ProviderConnections.model_validate(
        {
            'connections': {
                'old': {
                    'id': 'old',
                    'display_name': 'Former gateway',
                    'base_url': 'https://former.example/v1',
                    'api_key': 'hidden',
                }
            }
        }
    )
    monkeypatch.setattr(
        org_profiles, '_get_org', AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(
        org_profiles,
        '_load_profiles_with_live_default',
        AsyncMock(return_value=profiles),
    )
    monkeypatch.setattr(org_profiles, '_load_connections', lambda _: connections)
    response = await org_profiles.list_profiles(uuid4(), 'user')
    serialized = response.model_dump(mode='json')
    assert serialized['profiles'][0]['requires_litellm'] is True
    assert serialized['profiles'][0]['base_url'] == 'https://former.example/v1'
    assert 'hidden' not in json.dumps(serialized)
