"""Relay + token endpoint through the real authorizer. Sandbox service and DB-backed store calls are
stubbed at their module boundary; store behaviour itself is covered in tests/unit/storage."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.customer_gateway import routes, service
from server.customer_gateway.relay import InferenceRelay, RelayConfig
from server.customer_gateway.tokens import mint_inference_token
from storage.customer_gateway_store import (
    CustomerGatewayDenied,
    MemberResolution,
    OrgGateway,
)

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
        'models': {'deepseek': 'deepseek/chat', 'sonnet': 'anthropic/sonnet'},
    },
)
RES = MemberResolution(KEY, 'fp', 2, 5, frozenset({'deepseek'}))
SB = dict(
    id='sb1',
    created_by_user_id=str(USER),
    credential_org_id=ORG,
    status=service.SandboxStatus.RUNNING,
)


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv(
        'CUSTOMER_GATEWAY_RELAY_URL', 'https://app/api/customer-gateway/v1'
    )
    monkeypatch.setenv(
        'CUSTOMER_GATEWAY_TOKEN_URL', 'https://app/api/customer-gateway/token'
    )
    with patch.object(service, '_jwt', return_value=JWT):
        yield


def _token(**kw):
    a = dict(
        user_id=str(USER),
        org_id=str(ORG),
        sandbox_id='sb1',
        gateway_id='gw',
        policy_version=5,
        ttl=timedelta(minutes=5),
    )
    a.update(kw)
    return mint_inference_token(JWT, **a)


def _client(upstream_seen, sandbox=SB, gw=GW, resolve=None, get_sandbox=None):
    def upstream(req):
        upstream_seen.append(req)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b'{"ok":true}'),
            headers={'content-type': 'application/json'},
        )

    relay = InferenceRelay(
        RelayConfig(),
        service.authorize_inference,
        httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    app = FastAPI()
    for r in relay.routes():
        app.add_route(
            '/api/customer-gateway' + r.path, r.endpoint, methods=list(r.methods)
        )
    patches = [
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            service,
            'get_sandbox',
            get_sandbox
            or AsyncMock(return_value=SimpleNamespace(**sandbox) if sandbox else None),
        ),
        patch.object(
            service.CustomerGatewayStore,
            'resolve',
            resolve or AsyncMock(return_value=RES),
        ),
    ]
    for p in patches:
        p.start()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://app'
    ), patches


async def _post(seen, headers, body=None, **kw):
    c, patches = _client(seen, **kw)
    try:
        return await c.post(
            '/api/customer-gateway/v1/chat/completions',
            headers=headers,
            json=body
            or {'model': 'deepseek', 'messages': [{'role': 'user', 'content': 'hi'}]},
        )
    finally:
        for p in patches:
            p.stop()


async def test_allowed_request_injects_customer_key_server_side():
    seen = []
    r = await _post(
        seen,
        {
            'Authorization': f'Bearer {_token()}',
            'X-Api-Key': 'forged',
            'X-OpenHands-User': 'someone-else',
        },
    )
    assert r.status_code == 200 and KEY not in r.text
    up = seen[0]
    assert (
        up.headers['authorization'] == f'Bearer {KEY}'
        and 'x-api-key' not in up.headers
        and 'x-openhands-user' not in up.headers
    )
    assert str(up.url) == 'https://gw.customer.example/v1/chat/completions'
    assert b'"deepseek/chat"' in up.content


@pytest.mark.parametrize(
    'case,headers,kw,code',
    [
        ('missing', {}, {}, 401),
        ('session key only', {'X-Session-API-Key': 'sess'}, {}, 401),
        (
            'wrong audience',
            {
                'Authorization': 'Bearer '
                + JWT.create_jws_token(
                    {'sub': 'u'},
                    expires_in=timedelta(minutes=5),
                    audience=JwtAudience.SIGNUP_LINK,
                )
            },
            {},
            401,
        ),
        (
            'paused sandbox',
            None,
            {'sandbox': {**SB, 'status': service.SandboxStatus.PAUSED}},
            403,
        ),
        (
            'other owner',
            None,
            {'sandbox': {**SB, 'created_by_user_id': str(uuid4())}},
            403,
        ),
        ('other org', None, {'sandbox': {**SB, 'credential_org_id': uuid4()}}, 403),
        ('missing sandbox', None, {'sandbox': None}, 403),
        ('gateway off', None, {'gw': None}, 403),
        (
            'revoked',
            None,
            {'resolve': AsyncMock(side_effect=CustomerGatewayDenied('token_revoked'))},
            403,
        ),
    ],
)
async def test_denials_never_reach_upstream(case, headers, kw, code):
    seen = []
    r = await _post(
        seen,
        headers if headers is not None else {'Authorization': f'Bearer {_token()}'},
        **kw,
    )
    assert r.status_code == code, case
    assert not seen


async def test_sandbox_lookup_failure_is_retryable_not_denied():
    """A transient failure to confirm the sandbox's live status (e.g. a runtime-API
    error) must be a retryable 503, not the non-retryable 403 used for a sandbox
    that is actually confirmed inactive -- see service.CustomerGatewaySandboxLookupUnavailable.
    """
    seen = []
    r = await _post(
        seen,
        {'Authorization': f'Bearer {_token()}'},
        get_sandbox=AsyncMock(
            side_effect=service.CustomerGatewaySandboxLookupUnavailable(
                'sandbox_status_unknown'
            )
        ),
    )
    assert r.status_code == 503
    assert r.json()['error']['code'] == 'sandbox_status_unknown'
    assert r.headers.get('retry-after') == '1'
    assert not seen


async def test_models_sandbox_lookup_failure_is_retryable_not_denied():
    seen = []
    c, patches = _client(
        seen,
        get_sandbox=AsyncMock(
            side_effect=service.CustomerGatewaySandboxLookupUnavailable(
                'sandbox_status_unknown'
            )
        ),
    )
    try:
        r = await c.get(
            '/api/customer-gateway/v1/models',
            headers={'Authorization': f'Bearer {_token()}'},
        )
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 503
    assert r.json()['error']['code'] == 'sandbox_status_unknown'


async def test_model_not_permitted_for_member():
    seen = []
    r = await _post(
        seen,
        {'Authorization': f'Bearer {_token()}'},
        {'model': 'sonnet', 'messages': []},
    )
    assert (
        r.status_code == 403
        and r.json()['error']['code'] == 'model_not_permitted'
        and not seen
    )


async def _token_endpoint(sandbox_or_exc, gw=GW, pv=None):
    pv = pv or AsyncMock(return_value=5)
    app = FastAPI()
    app.include_router(routes.build_router())
    v = (
        AsyncMock(side_effect=sandbox_or_exc)
        if isinstance(sandbox_or_exc, Exception)
        else AsyncMock(return_value=sandbox_or_exc)
    )
    with (
        patch.object(routes, 'validate_session_key', v),
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(service.CustomerGatewayStore, 'current_policy_version', pv),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://app'
        ) as c:
            return await c.get(
                '/api/customer-gateway/token', headers={'X-Session-API-Key': 'sess'}
            )


async def test_token_endpoint_mints_sandbox_bound_token_not_key():
    r = await _token_endpoint(SimpleNamespace(**SB))
    assert r.status_code == 200 and KEY not in r.text
    p = service.verify_inference_token(JWT, r.text)
    assert (p.user_id, p.org_id, p.session_id, p.policy_version) == (
        str(USER),
        str(ORG),
        'sb1',
        5,
    )


async def test_token_endpoint_denials():
    assert (
        await _token_endpoint(HTTPException(401, 'Invalid session API key'))
    ).status_code == 401
    assert (await _token_endpoint(SimpleNamespace(**SB), gw=None)).status_code == 404
    r = await _token_endpoint(
        SimpleNamespace(**SB),
        pv=AsyncMock(side_effect=CustomerGatewayDenied('credential_disabled')),
    )
    assert r.status_code == 403


def _assert_no_store(r):
    assert r.headers['cache-control'] == 'no-store, private'
    assert r.headers['pragma'] == 'no-cache'
    assert r.headers['vary'] == 'X-Session-API-Key'


async def test_token_endpoint_responses_are_never_cacheable():
    _assert_no_store(await _token_endpoint(SimpleNamespace(**SB)))
    _assert_no_store(
        await _token_endpoint(HTTPException(401, 'Invalid session API key'))
    )
    _assert_no_store(await _token_endpoint(SimpleNamespace(**SB), gw=None))
    _assert_no_store(
        await _token_endpoint(
            SimpleNamespace(**SB),
            pv=AsyncMock(side_effect=CustomerGatewayDenied('credential_disabled')),
        )
    )
