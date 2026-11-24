"""The LIVE Enterprise /api/v1/users/me route (saas_server overrides the OSS one) withholds
every stored LLM credential from the sandbox export while ON.

Runs in a subprocess because importing ``saas_server`` builds the full app (same pattern as
tests/unit/test_saas_server.py). The real app, route table, middleware, SaaS handler and
``_inject_sdk_compat_fields`` run; only the user context dependency, session-key lookup,
org-info lookup and the gateway store row are injected."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CANARY = 'sk-byok-canary-5d1e8f2a9b'

SCRIPT = textwrap.dedent(
    """
    import asyncio, json, os, sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import UUID

    import httpx
    from pydantic import SecretStr
    from starlette.routing import Match

    import saas_server
    from openhands.app_server.sandbox import session_auth
    from openhands.app_server.settings.provider_connections import ProviderConnections
    from openhands.app_server.user.user_models import UserInfo
    from openhands.sdk import LLM
    from server.customer_gateway import service
    from server.routes import users_v1

    CANARY, ORG, MODE = sys.argv[1], UUID(sys.argv[2]), sys.argv[3]
    scope = {'type': 'http', 'path': '/api/v1/users/me', 'root_path': '', 'method': 'GET',
             'scheme': 'http', 'headers': [], 'query_string': b'expose_secrets=true',
             'server': ('testserver', 80), 'client': ('testclient', 50000)}
    route = next(r for r in saas_server.app.router.routes if r.matches(scope)[0] == Match.FULL)
    assert route.endpoint is users_v1.get_current_user_saas, route.endpoint

    byok = LLM(model='openhands/claude-sonnet', base_url='https://llm-proxy.example',
               api_key=SecretStr(CANARY), usage_id='agent')
    user = UserInfo(id='u1')
    user = user.model_copy(update={
        'agent_settings': user.agent_settings.model_copy(update={'llm': byok})})
    ctx = MagicMock()
    ctx.get_user_info = AsyncMock(return_value=user)
    ctx.get_provider_connections = AsyncMock(return_value=ProviderConnections())
    sb_org = ORG if MODE != 'cross_org' else UUID(int=7)
    sb = SimpleNamespace(id='sb1', created_by_user_id='u1', credential_org_id=sb_org,
                         status=service.SandboxStatus.RUNNING)
    gw = service.OrgGateway(ORG, 'gw', {'base_url': 'https://gw.example/v1', 'models': {'m': 'm'}})
    saas_server.app.dependency_overrides[users_v1.user_dependency.dependency] = lambda: ctx

    async def main():
        with (
            patch.object(users_v1, 'validate_session_key_ownership', AsyncMock()),
            patch.object(users_v1, '_get_org_info_from_context',
                         AsyncMock(return_value={'org_id': str(ORG)})),
            patch.object(session_auth, 'validate_session_key', AsyncMock(return_value=sb)),
            patch.object(service.CustomerGatewayStore, 'get_enabled_org_gateway',
                         AsyncMock(side_effect=lambda o: gw if o == ORG else None)),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=saas_server.app),
                                         base_url='http://testserver') as c:
                r = await c.get('/api/v1/users/me', params={'expose_secrets': 'true'},
                                headers={'X-Session-API-Key': 'sess'})
        print(json.dumps({'status': r.status_code, 'body': r.text}))

    asyncio.run(main())
    """
)


def _run(tmp_path, mode: str, enabled: bool) -> dict:
    frontend = tmp_path / 'frontend' / 'build'
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / 'index.html').write_text('<html></html>')
    repo_root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=f'{repo_root}:{env.get("PYTHONPATH", "")}',
        OPENHANDS_SUPPRESS_BANNER='1',
        POSTHOG_CLIENT_KEY='test-posthog-key',
        SERVE_FRONTEND='false',
        FRONTEND_DIRECTORY=str(frontend),
        CUSTOMER_GATEWAY_RELAY_URL='https://app.example/api/customer-gateway/v1',
        CUSTOMER_GATEWAY_TOKEN_URL='https://app.example/api/customer-gateway/token',
    )
    if enabled:
        env['CUSTOMER_GATEWAY_ENABLED'] = 'true'
    else:
        env.pop('CUSTOMER_GATEWAY_ENABLED', None)
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            SCRIPT,
            CANARY,
            '11111111-1111-1111-1111-111111111111',
            mode,
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.timeout(300)
def test_live_saas_users_me_withholds_all_llm_credentials_when_on(tmp_path):
    out = _run(tmp_path, 'same_org', enabled=True)
    assert out['status'] == 200
    assert CANARY not in out['body']
    assert 'llm-proxy.example' not in out['body']
    data = json.loads(out['body'])
    relay = 'https://app.example/api/customer-gateway/v1'
    assert data['customer_gateway']['llm_export'] == 'unsupported'
    assert data['llm_api_key'] == 'customer-gateway-no-standalone-llm'
    assert data['llm_base_url'] == relay
    assert data['llm_model'] == 'openai/customer-gateway-unsupported'
    llm = data['agent_settings']['llm']
    assert (llm['model'], llm['base_url'], llm['api_mode']) == (
        'openai/customer-gateway-unsupported',
        relay,
        'chat',
    )


@pytest.mark.timeout(300)
def test_live_saas_users_me_cross_org_sandbox_refused(tmp_path):
    out = _run(tmp_path, 'cross_org', enabled=True)
    assert out['status'] == 409
    assert CANARY not in out['body']


@pytest.mark.timeout(300)
def test_live_saas_users_me_unchanged_when_switch_off(tmp_path):
    out = _run(tmp_path, 'same_org', enabled=False)
    assert out['status'] == 200
    data = json.loads(out['body'])
    assert data['llm_api_key'] == CANARY
    assert 'customer_gateway' not in data
