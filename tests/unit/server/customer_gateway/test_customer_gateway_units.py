"""DB-free tests: feature-off identity, token contract, LLM binding, profile validation, env wiring."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from openhands.agent_server.models import StartConversationRequest
from openhands.app_server.sandbox.k8s_agent_sandbox_service import (
    K8sAgentSandboxServiceInjector,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxService,
    RemoteSandboxServiceInjector,
)
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.utils import customer_gateway_hooks
from openhands.app_server.utils.encryption_key import EncryptionKey
from openhands.sdk import LLM, Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.workspace import LocalWorkspace
from server.customer_gateway import service
from server.customer_gateway.config import get_settings
from server.customer_gateway.profile import ProfileError, parse_profile
from server.customer_gateway.tokens import (
    InvalidInferenceToken,
    mint_inference_token,
    verify_inference_token,
)

JWT = JwtService(
    keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
)
PROFILE = parse_profile(
    {
        'base_url': 'https://gw.customer.example/v1',
        'models': {
            'claude-sonnet': 'anthropic/claude-sonnet',
            'deepseek': 'deepseek/chat',
        },
    }
)
RELAY = 'https://app.example/api/customer-gateway/v1'
ON_ENV = {
    'CUSTOMER_GATEWAY_ENABLED': 'true',
    'CUSTOMER_GATEWAY_RELAY_URL': RELAY,
    'CUSTOMER_GATEWAY_TOKEN_URL': 'https://app.example/api/customer-gateway/token',
}
MANAGED_ENV = {
    'OH_LLM_API_KEY_REFRESH_URL': 'https://app.example/api/keys/llm/managed/current',
    'OH_LLM_API_KEY_REFRESH_HEADERS': '{"X-Session-API-Key": "${SESSION_API_KEY}"}',
    'OH_LLM_API_KEY_REFRESH_BASE_URLS': 'http://litellm:4000',
}


def _mint(**kw):
    args = dict(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        sandbox_id='sb1',
        gateway_id='gw',
        policy_version=1,
        ttl=timedelta(minutes=5),
    )
    args.update(kw)
    return mint_inference_token(JWT, **args)


@pytest.fixture
def store_untouched():
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(side_effect=AssertionError('DB touched while OFF')),
    ):
        yield


@pytest.fixture
def remote_runtime():
    injector = RemoteSandboxServiceInjector(
        api_url='http://runtime-api', api_key='test'
    )
    with patch.object(
        service, 'get_global_config', return_value=SimpleNamespace(sandbox=injector)
    ):
        yield


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv('CUSTOMER_GATEWAY_ENABLED', raising=False)


def _request():
    agent = Agent(
        llm=LLM(
            model='litellm_proxy/claude-sonnet',
            base_url='http://litellm:4000',
            api_key=SecretStr('managed'),
            usage_id='agent',
        ),
        condenser=LLMSummarizingCondenser(
            llm=LLM(
                model='litellm_proxy/deepseek',
                base_url='http://litellm:4000',
                api_key=SecretStr('managed'),
                usage_id='condenser',
            )
        ),
    )
    return StartConversationRequest(
        agent=agent, workspace=LocalWorkspace(working_dir='/tmp')
    )


def _sandbox(**kw):
    return SimpleNamespace(
        **{
            'id': 'sb1',
            'created_by_user_id': str(uuid4()),
            'credential_org_id': uuid4(),
            'status': service.SandboxStatus.RUNNING,
            'created_at': datetime(2026, 11, 15, tzinfo=UTC),
            **kw,
        }
    )


async def test_off_hooks_are_identity_without_db(off, store_untouched):
    req, sb = _request(), _sandbox()
    assert await customer_gateway_hooks.bind_start_request(req, sb) is req
    llm = req.agent.llm
    assert await customer_gateway_hooks.bind_profile_llm(llm, sb) is llm
    env = dict(MANAGED_ENV)
    assert (
        await customer_gateway_hooks.sandbox_environment(sb.credential_org_id, env)
        == MANAGED_ENV
    )
    assert (
        await customer_gateway_hooks.gateway_enabled_for_org(sb.credential_org_id)
        is False
    )


def test_off_router_not_built(off):
    from server.customer_gateway.config import get_settings

    assert get_settings() is None


def test_token_roundtrip_and_claims():
    p = verify_inference_token(JWT, _mint(sandbox_id='sbX', policy_version=7))
    assert (p.session_id, p.policy_version, p.gateway_id) == ('sbX', 7, 'gw')


@pytest.mark.parametrize(
    'audience', [a for a in JwtAudience if a is not JwtAudience.INFERENCE]
)
def test_other_audiences_rejected(audience):
    tok = JWT.create_jws_token(
        {'iss': 'x', 'sub': 'u'}, expires_in=timedelta(minutes=5), audience=audience
    )
    with pytest.raises(InvalidInferenceToken):
        verify_inference_token(JWT, tok)


@pytest.mark.parametrize(
    'audience', [a for a in JwtAudience if a is not JwtAudience.INFERENCE]
)
def test_inference_token_rejected_by_every_other_audience(audience):
    with pytest.raises(jwt.InvalidTokenError):
        JWT.verify_jws_token(_mint(), audience=audience)


def test_tampered_expired_and_overlong_tokens():
    tok = _mint()
    with pytest.raises(InvalidInferenceToken):
        verify_inference_token(
            JWT, tok[:-2] + ('AA' if not tok.endswith('AA') else 'BB')
        )
    with pytest.raises(InvalidInferenceToken):
        verify_inference_token(
            JWT,
            JWT.create_jws_token(
                {'iss': 'x'},
                expires_in=timedelta(seconds=-5),
                audience=JwtAudience.INFERENCE,
            ),
        )
    with pytest.raises(ValueError):
        _mint(ttl=timedelta(hours=2))


def test_bind_llm_maps_catalog_and_hides_managed_key():
    llm = LLM(
        model='litellm_proxy/claude-sonnet',
        base_url='http://litellm:4000',
        api_key=SecretStr('managed'),
        litellm_extra_body={'metadata': {'x': 1}},
        usage_id='agent',
    )
    bound = service.bind_llm(llm, PROFILE, RELAY, 'inference-token')
    assert (bound.model, bound.base_url, bound.api_key.get_secret_value()) == (
        'openai/claude-sonnet',
        RELAY,
        'inference-token',
    )
    assert bound.litellm_extra_body == {} and bound.usage_id == 'agent'
    with pytest.raises(service.CustomerGatewayLaunchError):
        service.bind_llm(
            llm.model_copy(update={'model': 'anthropic/claude-opus'}),
            PROFILE,
            RELAY,
            't',
        )


@pytest.mark.usefixtures('remote_runtime')
async def test_on_binds_agent_and_condenser(monkeypatch):
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)
    sb = _sandbox()
    gw = _gw(sb.credential_org_id)
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(return_value=3),
        ),
        patch.object(service, '_jwt', return_value=JWT),
    ):
        out = await customer_gateway_hooks.bind_start_request(_request(), sb)
    for llm in (out.agent.llm, out.agent.condenser.llm):
        assert llm.base_url == RELAY
        p = verify_inference_token(JWT, llm.api_key.get_secret_value())
        assert (p.user_id, p.org_id, p.session_id, p.policy_version) == (
            sb.created_by_user_id,
            str(sb.credential_org_id),
            'sb1',
            3,
        )
    assert 'managed' not in out.model_dump_json(context={'expose_secrets': True})


async def test_on_env_rewires_refresh_only_for_gateway_org(monkeypatch):
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)
    org = uuid4()
    gw = service.OrgGateway(org, 'gw', {})
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(return_value=gw),
    ):
        env = await service.sandbox_env_overrides(org, dict(MANAGED_ENV))
    assert env['OH_LLM_API_KEY_REFRESH_URL'] == ON_ENV['CUSTOMER_GATEWAY_TOKEN_URL']
    assert env['OH_LLM_API_KEY_REFRESH_BASE_URLS'] == RELAY
    assert (
        env['OH_LLM_API_KEY_REFRESH_HEADERS']
        == MANAGED_ENV['OH_LLM_API_KEY_REFRESH_HEADERS']
    )
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(return_value=None),
    ):
        assert (
            await service.sandbox_env_overrides(org, dict(MANAGED_ENV)) == MANAGED_ENV
        )


@pytest.mark.parametrize(
    'bad', [{'base_url': 'http://x/v1'}, {'credential_header': 'host'}, {'models': {}}]
)
def test_profile_rejects(bad):
    with pytest.raises(ProfileError):
        parse_profile({'base_url': 'https://gw/v1', 'models': {'a': 'b'}, **bad})


def _on(monkeypatch):
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)


def _gw(org):
    return service.OrgGateway(
        org,
        'gw',
        {'base_url': PROFILE.base_url, 'models': PROFILE.models},
        datetime(2026, 11, 14, tzinfo=UTC),
    )


def _on_store(gw):
    return (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(return_value=1),
        ),
        patch.object(service, '_jwt', return_value=JWT),
    )


@pytest.mark.parametrize(
    'value,expected',
    [
        ('1', True),
        ('true', True),
        ('TRUE', True),
        ('yes', False),
        ('0', False),
        ('', False),
    ],
)
def test_boolean_env_flags_accept_1_and_true(monkeypatch, value, expected):
    monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', value)
    monkeypatch.setenv('CUSTOMER_GATEWAY_RELAY_URL', RELAY)
    monkeypatch.setenv('CUSTOMER_GATEWAY_TOKEN_URL', 'https://t')
    monkeypatch.setenv('CUSTOMER_GATEWAY_ALLOW_HTTP_TEST_ONLY', value)
    settings = get_settings()
    assert (settings is not None) is expected
    if settings:
        assert settings.allow_http is expected


def test_bound_llm_has_no_sdk_retries_and_off_keeps_default():
    llm = LLM(model='litellm_proxy/deepseek', usage_id='agent')
    assert llm.num_retries > 0
    assert service.bind_llm(llm, PROFILE, RELAY, 't').num_retries == 0


def test_responses_api_llm_refused():
    llm = LLM(model='litellm_proxy/deepseek', usage_id='agent', api_mode='responses')
    with pytest.raises(service.CustomerGatewayLaunchError):
        service.bind_llm(llm, PROFILE, RELAY, 't')


@pytest.mark.usefixtures('remote_runtime')
async def test_invariant_rejects_any_unbound_llm(monkeypatch):
    _on(monkeypatch)
    req = _request()
    half = req.model_copy(
        update={
            'agent': req.agent.model_copy(
                update={'llm': service.bind_llm(req.agent.llm, PROFILE, RELAY, _mint())}
            )
        }
    )
    with patch.object(service, '_jwt', return_value=JWT):
        with pytest.raises(service.CustomerGatewayLaunchError, match='condenser'):
            service.assert_all_llms_bound(half, RELAY)
        forged = service.bind_llm(
            req.agent.llm, PROFILE, RELAY, 'not-an-inference-token'
        )
        with pytest.raises(service.CustomerGatewayLaunchError, match='non-inference'):
            service.assert_all_llms_bound(forged, RELAY)


@pytest.mark.usefixtures('remote_runtime')
@pytest.mark.parametrize(
    'trigger',
    [
        'slack',
        'jira',
        'jira_dc',
        'linear',
        'bitbucket',
        'microagent_management',
    ],
)
async def test_non_interactive_launch_fails_closed_when_on(monkeypatch, trigger):
    _on(monkeypatch)
    sb = _sandbox()
    a, b, c = _on_store(_gw(sb.credential_org_id))
    with a, b, c, pytest.raises(service.CustomerGatewayLaunchError, match=trigger):
        await customer_gateway_hooks.bind_start_request(_request(), sb, trigger)


@pytest.mark.usefixtures('remote_runtime')
@pytest.mark.parametrize('trigger', ['automation', 'resolver'])
async def test_sandbox_owner_launch_binds_for_automation_and_resolver(
    monkeypatch, trigger
):
    """automation/resolver are owner-attributed, not ambient: the sandbox's own
    `created_by_user_id` (set upstream, before this hook runs — see
    webhook_router.py's own docstring for automation, and
    integrations/resolver_org_router.py + each *_view.py's own actor/org
    resolution for resolver, which is shared by every resolver integration, not
    only GitLab) is the only identity ever minted into the token. This exercises
    the real `bind_start_request` path via `live_status_app_conversation_service.py`'s
    actual call shape, not a hand-minted token."""
    _on(monkeypatch)
    sb = _sandbox()
    gw = _gw(sb.credential_org_id)
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(return_value=3),
        ),
        patch.object(service, '_jwt', return_value=JWT),
    ):
        out = await customer_gateway_hooks.bind_start_request(_request(), sb, trigger)
    llm = out.agent.llm
    assert llm.base_url == RELAY
    p = verify_inference_token(JWT, llm.api_key.get_secret_value())
    assert (p.user_id, p.org_id, p.session_id) == (
        sb.created_by_user_id,
        str(sb.credential_org_id),
        sb.id,
    )


@pytest.mark.usefixtures('remote_runtime')
@pytest.mark.parametrize(
    'trigger,denial',
    [
        # Unmapped resolver actor / automation owner with no org-member row at all.
        ('automation', 'not_a_member'),
        ('resolver', 'not_a_member'),
        # A real member+credential row whose *account* is not human — this is the
        # actual check in storage/customer_gateway_store.py:CustomerGatewayStore._load,
        # which fires even though membership and a credential both exist, not merely
        # "no such member row".
        ('automation', 'service_account'),
        ('resolver', 'service_account'),
    ],
)
async def test_sandbox_owner_launch_still_denies_non_human_or_unmapped(
    monkeypatch, trigger, denial
):
    """Allowlisting the trigger must not bypass the existing
    membership/account-type/credential checks in CustomerGatewayStore.resolve()."""
    _on(monkeypatch)
    sb = _sandbox()
    gw = _gw(sb.credential_org_id)
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(side_effect=service.CustomerGatewayDenied(denial)),
        ),
        pytest.raises(service.CustomerGatewayLaunchError, match=denial),
    ):
        await customer_gateway_hooks.bind_start_request(_request(), sb, trigger)


async def test_org_gateway_for_sandbox_keys_only_on_sandboxs_own_org(monkeypatch):
    """A resolver conversation mis-routed to the wrong org (e.g. resolve_org_for_repo
    falling back to a personal/unclaimed-repo org per its own documented contract)
    must not pick up a *different* org's gateway — org_gateway_for_sandbox looks up
    only `sandbox.credential_org_id`, never any other org, regardless of which other
    orgs have a gateway enabled."""
    _on(monkeypatch)
    sb = _sandbox()
    other_org = uuid4()
    assert other_org != sb.credential_org_id
    looked_up: list = []

    async def fake_get_enabled_org_gateway(org_id):
        looked_up.append(org_id)
        if org_id == other_org:
            return _gw(other_org)
        return None

    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        fake_get_enabled_org_gateway,
    ):
        result = await service.org_gateway_for_sandbox(sb)
    assert result is None
    assert looked_up == [sb.credential_org_id]


async def test_non_interactive_launch_unchanged_when_off(off, store_untouched):
    req = _request()
    assert (
        await customer_gateway_hooks.bind_start_request(req, _sandbox(), 'automation')
        is req
    )


async def test_warm_pool_backend_refused(monkeypatch):
    _on(monkeypatch)
    sb = _sandbox()
    a, b, c = _on_store(_gw(sb.credential_org_id))
    cfg = SimpleNamespace(sandbox=K8sAgentSandboxServiceInjector())
    with (
        a,
        b,
        c,
        patch.object(service, 'get_global_config', return_value=cfg),
        pytest.raises(service.CustomerGatewayLaunchError, match='K8sAgent'),
    ):
        await customer_gateway_hooks.bind_start_request(_request(), sb, 'gui')


async def test_resume_of_pre_gateway_sandbox_refused(monkeypatch):
    _on(monkeypatch)
    org = uuid4()
    gw = _gw(org)
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(return_value=gw),
    ):
        with pytest.raises(HTTPException) as exc:
            await customer_gateway_hooks.assert_resume_allowed(
                org, gw.enabled_since - timedelta(minutes=1)
            )
        assert exc.value.status_code == 409 and 'predates' in exc.value.detail
        await customer_gateway_hooks.assert_resume_allowed(
            org, gw.enabled_since + timedelta(minutes=1)
        )


@pytest.mark.usefixtures('remote_runtime')
async def test_running_sandbox_born_before_enable_cannot_start_or_bind(monkeypatch):
    """A RUNNING sandbox created before the OFF->ON switch: a new or sub-conversation
    naming it, and any profile/title LLM bound into it, are refused (not only resume)."""
    _on(monkeypatch)
    sb = _sandbox(created_at=datetime(2026, 11, 13, tzinfo=UTC))
    gw = _gw(sb.credential_org_id)
    mint = AsyncMock(side_effect=AssertionError('must refuse before minting'))
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(service, 'mint_for_sandbox', mint),
    ):
        with pytest.raises(service.CustomerGatewayLaunchError, match='predates'):
            await customer_gateway_hooks.bind_start_request(_request(), sb)
        with pytest.raises(service.CustomerGatewayLaunchError, match='predates'):
            await customer_gateway_hooks.bind_profile_llm(LLM(model='deepseek'), sb)
    mint.assert_not_called()


async def test_resume_off_untouched(off, store_untouched):
    await customer_gateway_hooks.assert_resume_allowed(
        uuid4(), datetime(2000, 1, 1, tzinfo=UTC)
    )


def _with_definition(**kw):
    from openhands.sdk.subagent.schema import AgentDefinition

    return _request().model_copy(
        update={
            'agent_definitions': [AgentDefinition(name='helper', description='d', **kw)]
        }
    )


@pytest.mark.usefixtures('remote_runtime')
async def test_definition_with_custom_profile_store_refused(monkeypatch):
    _on(monkeypatch)
    sb = _sandbox()
    a, b, c = _on_store(_gw(sb.credential_org_id))
    req = _with_definition(model='p', profile_store_dir='/tmp/x')
    with a, b, c, pytest.raises(service.CustomerGatewayLaunchError, match='custom'):
        await customer_gateway_hooks.bind_start_request(req, sb, 'gui')


@pytest.mark.usefixtures('remote_runtime')
async def test_definition_profile_name_kept_and_definition_condenser_bound(monkeypatch):
    _on(monkeypatch)
    sb = _sandbox()
    cond = LLMSummarizingCondenser(
        llm=LLM(
            model='litellm_proxy/deepseek',
            base_url='http://litellm:4000',
            api_key=SecretStr('managed'),
            usage_id='def-cond',
        )
    )
    a, b, c = _on_store(_gw(sb.credential_org_id))
    with a, b, c:
        out = await customer_gateway_hooks.bind_start_request(
            _with_definition(model='p', condenser=cond), sb, 'gui'
        )
    d = out.agent_definitions[0]
    assert d.model == 'p' and d.condenser.llm.base_url == RELAY


async def test_refresh_hook_registered_for_bound_and_switched_llms_only(monkeypatch):
    """Real SDK 1.62 registration using the env our sandbox hook produces. A switch installs
    a new agent and the agent server re-runs this registration (event_service callback)."""
    from openhands.agent_server.managed_llm_key import (
        register_managed_llm_key_refresh,
    )

    _on(monkeypatch)
    sb = _sandbox()
    base = {'OH_LLM_API_KEY_REFRESH_HEADERS': json.dumps({'X-Session-API-Key': 's'})}
    gw = _gw(sb.credential_org_id)
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(return_value=gw),
    ):
        env = await customer_gateway_hooks.sandbox_environment(
            sb.credential_org_id, base
        )
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    bound = service.bind_llm(_request().agent.llm, PROFILE, RELAY, 't')
    assert register_managed_llm_key_refresh(Agent(llm=bound)) == 1
    switched = service.bind_llm(
        LLM(model='litellm_proxy/deepseek', usage_id='switched'), PROFILE, RELAY, 't'
    )
    assert register_managed_llm_key_refresh(Agent(llm=switched)) == 1
    managed = _request().agent.model_copy(update={'condenser': None})
    assert register_managed_llm_key_refresh(managed) == 0


async def test_resume_route_returns_409_not_500_for_pre_gateway_sandbox(monkeypatch):
    """Real sandbox router + real RemoteSandboxService.resume_sandbox; only the stored
    sandbox row lookup is injected. The runtime API is never reached."""
    from fastapi import FastAPI

    from openhands.app_server.sandbox import sandbox_router

    _on(monkeypatch)
    org = uuid4()
    gw = _gw(org)
    stored = SimpleNamespace(
        credential_org_id=org, created_at=gw.enabled_since - timedelta(days=1)
    )
    svc = object.__new__(RemoteSandboxService)
    runtime = AsyncMock(side_effect=AssertionError('runtime must not be called'))
    app = FastAPI()
    app.include_router(sandbox_router.router)
    app.dependency_overrides[sandbox_router.sandbox_service_dependency.dependency] = (
        lambda: svc
    )
    app.dependency_overrides[sandbox_router.user_context_dependency.dependency] = (
        lambda: SimpleNamespace()
    )
    app.dependency_overrides[sandbox_router.db_session_dependency.dependency] = (
        lambda: (SimpleNamespace(commit=AsyncMock()))
    )
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            RemoteSandboxService,
            '_get_stored_sandbox',
            AsyncMock(return_value=stored),
        ),
        patch.object(RemoteSandboxService, '_get_runtime_for_resume', runtime),
    ):
        route = next(
            r for r in app.routes if getattr(r, 'name', '') == 'resume_sandbox'
        )
        path = route.path.replace('{sandbox_id}', 'sb1')
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://t'
        ) as c:
            r = await c.request(next(iter(route.methods)), path)
    assert r.status_code == 409
    assert 'predates the customer gateway' in r.json()['detail']
    runtime.assert_not_called()


async def test_get_sandbox_raises_unavailable_on_transient_runtime_lookup_error():
    """server.customer_gateway.service.get_sandbox must distinguish a transient
    runtime-API lookup failure (RemoteSandboxService.get_sandbox_or_raise raising
    SandboxRuntimeLookupError) from a confirmed-inactive sandbox, so the relay can
    answer with a retryable error instead of a non-retryable denial."""
    stored = SimpleNamespace(id='sb1')
    svc = object.__new__(RemoteSandboxService)
    with (
        patch.object(
            service,
            'get_sandbox_service',
            lambda state, request=None: _async_ctx(svc),
        ),
        patch.object(
            RemoteSandboxService, '_get_stored_sandbox', AsyncMock(return_value=stored)
        ),
        patch.object(
            RemoteSandboxService,
            '_get_runtime',
            AsyncMock(side_effect=RuntimeError('runtime API timed out')),
        ),
    ):
        with pytest.raises(service.CustomerGatewaySandboxLookupUnavailable):
            await service.get_sandbox('sb1')


async def test_get_sandbox_returns_none_for_confirmed_missing_runtime():
    stored = SimpleNamespace(id='sb1')
    svc = object.__new__(RemoteSandboxService)
    response = httpx.Response(
        404, request=httpx.Request('GET', 'https://runtime.example')
    )
    with (
        patch.object(
            service,
            'get_sandbox_service',
            lambda state, request=None: _async_ctx(svc),
        ),
        patch.object(
            RemoteSandboxService, '_get_stored_sandbox', AsyncMock(return_value=stored)
        ),
        patch.object(
            RemoteSandboxService,
            '_get_runtime',
            AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    'record missing', request=response.request, response=response
                )
            ),
        ),
    ):
        assert await service.get_sandbox('sb1') is None


async def test_get_sandbox_still_returns_none_for_confirmed_missing_stored_record():
    """A sandbox with no stored record at all (genuinely never existed / deleted) is
    still reported as None (definitive), not as a lookup failure."""
    svc = object.__new__(RemoteSandboxService)
    with (
        patch.object(
            service,
            'get_sandbox_service',
            lambda state, request=None: _async_ctx(svc),
        ),
        patch.object(
            RemoteSandboxService, '_get_stored_sandbox', AsyncMock(return_value=None)
        ),
    ):
        assert await service.get_sandbox('does-not-exist') is None


class _async_ctx:
    """Minimal async context manager yielding a pre-built sandbox service, standing
    in for RemoteSandboxServiceInjector.context in these dispatch-only tests."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


async def test_off_start_title_does_no_credential_org_lookup(monkeypatch):
    from openhands.app_server.app_conversation.live_status_app_conversation_service import (
        LiveStatusAppConversationService,
    )
    from openhands.app_server.sandbox import sandbox_store

    monkeypatch.delenv('CUSTOMER_GATEWAY_ENABLED', raising=False)
    lookup = AsyncMock(side_effect=AssertionError('OFF must not resolve the org'))
    svc = object.__new__(LiveStatusAppConversationService)
    svc.user_context = SimpleNamespace()
    with patch.object(sandbox_store, 'get_credential_org_id', lookup):
        await svc._start_title_llm(
            SimpleNamespace(agent_settings=None, llm_profiles=None),
            SimpleNamespace(llm_model=None),
        )
    lookup.assert_not_called()
