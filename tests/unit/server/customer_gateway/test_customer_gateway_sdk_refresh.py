"""Real SDK 1.62 LLM against the relay over HTTP: an expired inference token is a relay 401 that
the SDK refreshes and retries once; a customer-gateway key rejection is 424 and never refreshes."""

import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from openhands.sdk import LLM, Message, TextContent
from server.customer_gateway import service
from server.customer_gateway.relay import InferenceRelay, RelayConfig
from server.customer_gateway.tokens import ISSUER, SCOPE, mint_inference_token
from storage.customer_gateway_store import MemberResolution, OrgGateway

JWT = JwtService(
    keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
)
KEY = 'sk-customer-test-0123456789'
ORG, USER = uuid4(), uuid4()
GW = OrgGateway(
    ORG,
    'gw',
    {
        'base_url': 'https://gw.customer.example/v1',
        'models': {'deepseek': 'deepseek/chat'},
    },
    datetime(2026, 11, 14, tzinfo=UTC),
)
SB = SimpleNamespace(
    id='sb1',
    created_by_user_id=str(USER),
    credential_org_id=ORG,
    status=service.SandboxStatus.RUNNING,
    created_at=datetime(2026, 11, 15, tzinfo=UTC),
)
OK = {
    'id': 'x',
    'object': 'chat.completion',
    'created': 0,
    'model': 'deepseek/chat',
    'choices': [
        {
            'index': 0,
            'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': 'pong'},
        }
    ],
    'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
}


def _resp(status, body):
    # Unread stream, like a real upstream; the relay streams successful bodies through.
    return httpx.Response(
        status,
        stream=httpx.ByteStream(json.dumps(body).encode()),
        headers={'content-type': 'application/json'},
    )


def _expired_token():
    now = int(time.time())
    return JWT.create_jws_token(
        {
            'iss': ISSUER,
            'sub': str(USER),
            'org': str(ORG),
            'sid': 'sb1',
            'gw': 'gw',
            'jti': 'j',
            'scope': SCOPE,
            'pv': 1,
            'iat': now - 600,
        },
        expires_in=timedelta(seconds=-60),
        audience=JwtAudience.INFERENCE,
    )


def _fresh_token():
    return mint_inference_token(
        JWT,
        user_id=str(USER),
        org_id=str(ORG),
        sandbox_id='sb1',
        gateway_id='gw',
        policy_version=1,
        ttl=timedelta(minutes=5),
    )


@pytest.fixture
def relay_url(monkeypatch, request):
    upstream_status = request.param
    upstream_calls = []

    def upstream(req):
        upstream_calls.append(req)
        if upstream_status == 401:
            return _resp(401, {'error': {'message': f'invalid key {KEY}'}})
        return _resp(200, OK)

    monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv('CUSTOMER_GATEWAY_RELAY_URL', 'x')
    monkeypatch.setenv('CUSTOMER_GATEWAY_TOKEN_URL', 'x')
    relay = InferenceRelay(
        RelayConfig(),
        service.authorize_inference,
        httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    app = FastAPI()
    for r in relay.routes():
        app.add_route(r.path, r.endpoint, methods=list(r.methods))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
    patches = [
        patch.object(service, '_jwt', return_value=JWT),
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=GW),
        ),
        patch.object(service, 'get_sandbox', AsyncMock(return_value=SB)),
        patch.object(
            service.CustomerGatewayStore,
            'resolve',
            AsyncMock(
                return_value=MemberResolution(KEY, 'fp', 1, 1, frozenset({'deepseek'}))
            ),
        ),
    ]
    for p in patches:
        p.start()
    t = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    yield f'http://127.0.0.1:{port}/v1', upstream_calls
    server.should_exit = True
    t.join(5)
    for p in patches:
        p.stop()


def _llm(base_url):
    return LLM(
        model='openai/deepseek',
        base_url=base_url,
        api_key=SecretStr(_expired_token()),
        usage_id='agent',
        num_retries=0,
    )


def _msg():
    return [Message(role='user', content=[TextContent(text='ping')])]


@pytest.mark.parametrize('relay_url', [200], indirect=True)
def test_expired_inference_token_refreshes_once_and_succeeds(relay_url):
    url, upstream_calls = relay_url
    llm = _llm(url)
    refreshes = []
    llm.set_api_key_refresh_hook(lambda: refreshes.append(1) or _fresh_token())
    resp = llm.completion(_msg())
    assert refreshes == [1] and len(upstream_calls) == 1
    assert 'pong' in str(resp.message.content)


@pytest.mark.parametrize('relay_url', [401], indirect=True)
def test_customer_key_rejection_is_424_without_refresh(relay_url):
    url, upstream_calls = relay_url
    llm = _llm(url).model_copy(update={'api_key': SecretStr(_fresh_token())})
    refreshes = []
    llm.set_api_key_refresh_hook(lambda: refreshes.append(1) or _fresh_token())
    with pytest.raises(Exception) as exc:
        llm.completion(_msg())
    assert refreshes == [] and len(upstream_calls) == 1
    assert 'customer_gateway_key_rejected' in str(exc.value) and KEY not in str(
        exc.value
    )


@pytest.mark.parametrize('relay_url', [200], indirect=True)
async def test_standalone_get_llm_from_export_fails_with_gateway_message(relay_url):
    """Mirror of openhands-workspace 1.62.0 CloudWorkspace.get_llm(profile_name) (workspace.py
    585-644): ``LLM(**profiles[name], usage_id=...)`` from the sanitized users/me export."""
    url, upstream_calls = relay_url
    exported = {
        'llm_profiles': {
            'profiles': {
                'p': {
                    'model': 'openai/deepseek',
                    'base_url': 'http://managed:4000',
                    'api_key': 'sk-managed-canary',
                    'usage_id': 'p',
                }
            }
        }
    }
    with patch.object(
        service, 'get_settings', return_value=SimpleNamespace(relay_url=url)
    ):
        with patch.object(
            service, 'org_gateway_for_sandbox', AsyncMock(return_value=GW)
        ):
            payload = await service.sanitize_sandbox_user_export(exported, SB)
    assert payload['customer_gateway']['llm_export'] == 'unsupported'
    kwargs = dict(payload['llm_profiles']['profiles']['p'])
    kwargs['usage_id'] = 'profile:p'
    llm = LLM(**kwargs, num_retries=0)
    with pytest.raises(Exception) as exc:
        llm.completion(_msg())
    text = str(exc.value)
    assert 'sk-managed-canary' not in text
    assert 'Customer gateway: no OpenHands inference token' in text
    assert 'CloudWorkspace.get_llm' in text
    assert upstream_calls == []
