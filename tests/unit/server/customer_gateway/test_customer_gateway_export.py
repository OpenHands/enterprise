"""Sandbox-facing LLM-config export surfaces while ON: no stored managed LLM key leaves the app.

Native handlers are called directly; only the sandbox/session lookup and the store are stubbed."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from openhands.app_server.sandbox import session_auth
from openhands.app_server.settings.llm_profiles import LLMProfiles
from openhands.app_server.user import user_router
from openhands.app_server.user.user_models import UserInfo
from openhands.sdk import LLM
from server.customer_gateway import service
from server.routes import api_keys

CANARY = 'sk-managed-canary-7f3e9a1b2c'
ORG = uuid4()
SB = SimpleNamespace(
    id='sb1',
    created_by_user_id=str(uuid4()),
    credential_org_id=ORG,
    status=service.SandboxStatus.RUNNING,
)
GW = service.OrgGateway(
    ORG, 'gw', {'base_url': 'https://gw.example/v1', 'models': {'m': 'm'}}
)


def _user():
    managed = LLM(
        model='litellm_proxy/claude',
        base_url='http://litellm:4000',
        api_key=SecretStr(CANARY),
        usage_id='agent',
    )
    user = UserInfo(id='u1')
    settings = user.agent_settings.model_copy(update={'llm': managed})
    return user.model_copy(
        update={
            'agent_settings': settings,
            'llm_profiles': LLMProfiles(
                profiles={'p': managed.model_copy(update={'usage_id': 'p'})}
            ),
        }
    )


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv(
        'CUSTOMER_GATEWAY_RELAY_URL', 'https://app/api/customer-gateway/v1'
    )
    monkeypatch.setenv(
        'CUSTOMER_GATEWAY_TOKEN_URL', 'https://app/api/customer-gateway/token'
    )


async def _users_me(gw, lookup=None):
    ctx = MagicMock()
    ctx.get_user_info = AsyncMock(return_value=_user())
    with (
        patch.object(user_router, 'validate_session_key_ownership', AsyncMock()),
        patch.object(
            session_auth,
            'validate_session_key',
            lookup or AsyncMock(return_value=SB),
        ),
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
    ):
        resp = await user_router.get_current_user(
            user_context=ctx, expose_secrets=True, x_session_api_key='sess'
        )
    return resp.body.decode()


async def test_users_me_expose_secrets_withholds_managed_key_when_on(on):
    body = await _users_me(GW)
    assert CANARY not in body
    data = json.loads(body)
    assert data['id'] == 'u1'
    assert data['customer_gateway']['llm_export'] == 'unsupported'
    for llm in (data['llm_profiles']['profiles']['p'], data['agent_settings']['llm']):
        assert llm['base_url'] == 'https://app/api/customer-gateway/v1'
        assert llm['api_key'] == service.UNSUPPORTED_EXPORT_PLACEHOLDER
        assert llm['model'] == service.EXPORT_MODEL and llm['api_mode'] == 'chat'
    assert 'litellm' not in body
    unchanged = _user().model_dump(mode='json', context={'expose_secrets': True})
    llm_keys = {'agent_settings', 'llm_profiles', 'customer_gateway'}
    assert {k: v for k, v in data.items() if k not in llm_keys} == {
        k: v for k, v in unchanged.items() if k not in llm_keys
    }


async def test_users_me_unchanged_for_org_without_gateway(on):
    assert CANARY in await _users_me(None)


async def test_users_me_unchanged_when_globally_off(monkeypatch):
    monkeypatch.delenv('CUSTOMER_GATEWAY_ENABLED', raising=False)
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(side_effect=AssertionError),
    ):
        lookup = AsyncMock(side_effect=AssertionError('extra OFF session lookup'))
        assert CANARY in await _users_me(GW, lookup)
        lookup.assert_not_called()


@pytest.mark.parametrize('on_org', ['payload', 'sandbox'])
async def test_users_me_cross_org_sandbox_refused_when_either_org_is_on(on, on_org):
    other = uuid4()
    payload = {'org_id': str(other), 'llm_api_key': CANARY}
    gws = {other: GW} if on_org == 'payload' else {ORG: GW}
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(side_effect=lambda o: gws.get(o)),
    ):
        with pytest.raises(HTTPException) as exc:
            await service.sanitize_sandbox_user_export(payload, SB)
    assert exc.value.status_code == 409 and CANARY not in str(exc.value.detail)


async def _managed_current(gw):
    req = MagicMock()
    req.headers = {'X-Session-API-Key': 'sess'}
    store = MagicMock()
    store.resolve_valid_managed_llm_key = AsyncMock(return_value=CANARY)
    with (
        patch.object(api_keys, 'validate_session_key', AsyncMock(return_value=SB)),
        patch.object(
            api_keys.SaasSettingsStore, 'get_instance', AsyncMock(return_value=store)
        ),
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
    ):
        return await api_keys.get_managed_llm_key_for_sandbox(req)


async def test_managed_key_refresh_refused_for_gateway_sandbox(on):
    with pytest.raises(HTTPException) as exc:
        await _managed_current(GW)
    assert exc.value.status_code == 404 and CANARY not in str(exc.value.detail)


async def test_managed_key_refresh_unchanged_for_org_without_gateway(on):
    assert (await _managed_current(None)).body.decode() == CANARY


async def test_byor_export_refused_for_gateway_org_any_caller(on):
    with patch.object(
        service.CustomerGatewayStore,
        'get_enabled_org_gateway',
        AsyncMock(return_value=GW),
    ):
        with pytest.raises(HTTPException) as exc:
            await api_keys.refuse_customer_gateway_org(effective_org_id=ORG)
        assert exc.value.status_code == 403
        permitted = await api_keys.check_byor_permitted(
            user_id='u1', effective_org_id=ORG
        )
        assert permitted.permitted is False


@pytest.mark.parametrize('global_on', [True, False])
async def test_byor_export_unchanged_without_gateway(monkeypatch, global_on):
    if global_on:
        monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', 'true')
        monkeypatch.setenv('CUSTOMER_GATEWAY_RELAY_URL', 'https://app/r/v1')
        monkeypatch.setenv('CUSTOMER_GATEWAY_TOKEN_URL', 'https://app/t')
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=None)
            if global_on
            else AsyncMock(side_effect=AssertionError),
        ),
        patch.object(
            api_keys.OrgService,
            'check_byor_export_enabled',
            AsyncMock(return_value=True),
        ),
    ):
        await api_keys.refuse_customer_gateway_org(effective_org_id=ORG)
        permitted = await api_keys.check_byor_permitted(
            user_id='u1', effective_org_id=ORG
        )
        assert permitted.permitted is True


def test_byor_routes_carry_the_gateway_dependency():
    paths = {
        (r.path, tuple(sorted(r.methods))): [d.call for d in r.dependant.dependencies]
        for r in api_keys.api_router.routes
        if r.path.endswith('/llm/byor') or r.path.endswith('/llm/byor/refresh')
    }
    assert len(paths) == 2
    assert all(api_keys.refuse_customer_gateway_org in deps for deps in paths.values())
