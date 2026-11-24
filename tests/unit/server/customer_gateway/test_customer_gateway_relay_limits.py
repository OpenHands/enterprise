"""Relay bounds on the native mount: capacity, body size, redirects, error and cancel release.

Same authorizer and stubs as test_customer_gateway_relay.py; upstream is an httpx MockTransport."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
import httpx
import pytest
from starlette.requests import ClientDisconnect, Request

from server.customer_gateway import service
from server.customer_gateway.relay import (
    AUTHORIZATION_TIMEOUT_MESSAGE,
    InferenceRelay,
    RelayConfig,
)

from .test_customer_gateway_relay import GW, RES, SB, _token, on  # noqa: F401

BODY = {'model': 'deepseek', 'messages': [{'role': 'user', 'content': 'hi'}]}
STREAM_GW = type(GW)(
    GW.org_id, GW.gateway_id, {**GW.profile, 'capabilities': ['stream']}
)


@pytest.fixture
def stubs():
    with (
        patch.object(
            service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=STREAM_GW),
        ),
        patch.object(
            service, 'get_sandbox', AsyncMock(return_value=SimpleNamespace(**SB))
        ),
        patch.object(
            service.CustomerGatewayStore, 'resolve', AsyncMock(return_value=RES)
        ),
    ):
        yield


def _relay(handler, **cfg):
    seen: list[httpx.Request] = []

    def upstream(req):
        seen.append(req)
        return handler(req)

    relay = InferenceRelay(
        RelayConfig(**cfg),
        service.authorize_inference,
        httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    return relay, seen


def _request(body=BODY):
    raw = json.dumps(body).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {'type': 'http.disconnect'}
        sent = True
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/v1/chat/completions',
            'query_string': b'',
            'headers': [
                (b'authorization', f'Bearer {_token()}'.encode()),
                (b'content-type', b'application/json'),
                (b'content-length', str(len(raw)).encode()),
            ],
        },
        receive,
    )


def _ok(req):
    return httpx.Response(
        200,
        stream=httpx.ByteStream(b'{"ok":true}'),
        headers={'content-type': 'application/json'},
    )


async def test_capacity_429_without_upstream(stubs, monkeypatch):
    """Persistent fill: capacity never recovers, so the bounded wait still ends
    in a 429 with no upstream call, and no additional wait/queueing happens.
    CAPACITY_WAIT_S is shortened here so the (deliberately unmet) deadline
    doesn't make the test slow."""
    import server.customer_gateway.relay as relay_module

    monkeypatch.setattr(relay_module, 'CAPACITY_WAIT_S', 0.05)
    relay, seen = _relay(_ok, max_inflight=1)
    relay._inflight = 1
    resp = await relay.chat_completions(_request())
    assert resp.status_code == 429 and seen == []
    assert relay._waiting == 0


async def test_capacity_recovers_after_real_release_past_old_wait(stubs):
    """A real slot release ~300ms in -- longer than the old fixed 250ms wait
    this replaces -- still wakes the waiter and lets the request through.
    This is only possible because the wait is event-driven (woken by
    release(), bounded by one absolute deadline) rather than a fixed sleep:
    the old implementation would already have given up and returned 429 by
    the time this release happens."""
    relay, seen = _relay(_ok, max_inflight=1)
    held_release = relay._acquire('someone-else')
    assert held_release is not None

    async def release_later():
        await anyio.sleep(0.3)
        held_release()

    task = asyncio.create_task(release_later())
    try:
        resp = await relay.chat_completions(_request())
    finally:
        await task

    assert resp.status_code == 200
    assert len(seen) == 1
    assert relay._waiting == 0


async def test_capacity_wait_reauthorizes_and_denies_stale_policy(stubs):
    """A member resolution that goes stale (e.g. a rotated/removed model)
    while waiting for capacity must not reach upstream: reauthorization runs
    only once a slot is actually acquired, and the response reflects the
    fresh denial rather than the pre-wait snapshot."""
    from server.customer_gateway import service

    relay, seen = _relay(_ok, max_inflight=1)
    held_release = relay._acquire('someone-else')

    stale_res = SimpleNamespace(**{**RES.__dict__, 'allowed_models': frozenset()})
    resolve = AsyncMock(side_effect=[RES, stale_res])

    async def release_later():
        await anyio.sleep(0.05)
        held_release()  # capacity recovers, but policy changed underneath

    task = asyncio.create_task(release_later())
    try:
        with patch.object(service.CustomerGatewayStore, 'resolve', resolve):
            resp = await relay.chat_completions(_request())
    finally:
        await task

    assert resp.status_code == 403
    assert seen == []  # upstream never attempted
    assert resolve.await_count == 2  # the pre-wait result wasn't just reused
    assert relay._inflight == 0  # the capacity slot wasn't left held
    assert relay._waiting == 0


async def test_build_upstream_exception_releases_slot(stubs, monkeypatch):
    """A synchronous failure while building the upstream request (e.g. an
    invalid configured header) must still release the just-acquired slot."""
    relay, seen = _relay(_ok)

    def boom(*a, **kw):
        raise ValueError('bad header encoding')

    monkeypatch.setattr(relay, '_validate_and_build_upstream', boom)
    with pytest.raises(ValueError):
        await relay.chat_completions(_request())
    assert relay._inflight == 0 and relay._user_inflight == {} and seen == []


async def test_body_over_limit_413_without_upstream(stubs):
    relay, seen = _relay(_ok, max_body_bytes=64)
    big = {**BODY, 'messages': [{'role': 'user', 'content': 'x' * 200}]}
    resp = await relay.chat_completions(_request(big))
    assert resp.status_code == 413 and seen == [] and relay._inflight == 0


async def test_redirect_refused_and_slot_released(stubs):
    relay, seen = _relay(
        lambda r: httpx.Response(302, headers={'location': 'https://evil.example/'})
    )
    resp = await relay.chat_completions(_request())
    assert resp.status_code == 424 and len(seen) == 1 and relay._inflight == 0


async def test_upstream_error_releases_slot(stubs):
    relay, _ = _relay(
        lambda r: httpx.Response(500, stream=httpx.ByteStream(b'{"error":"x"}'))
    )
    resp = await relay.chat_completions(_request())
    assert resp.status_code == 500 and relay._inflight == 0


async def test_client_cancel_mid_stream_releases_slot_and_closes_upstream(stubs):
    closed = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                yield b'data: {"choices":[]}\n\n'

        async def aclose(self):
            closed.append(True)

    relay, _ = _relay(
        lambda r: httpx.Response(
            200, stream=Chunks(), headers={'content-type': 'text/event-stream'}
        )
    )
    resp = await relay.chat_completions(_request({**BODY, 'stream': True}))
    assert relay._inflight == 1
    sent = []

    async def send(message):
        sent.append(message)
        if len(sent) >= 3:
            raise OSError('client went away')

    async def receive():
        return {'type': 'http.disconnect'}

    with pytest.raises((OSError, ClientDisconnect)):
        await resp({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    assert relay._inflight == 0 and closed


async def test_per_user_cap_and_release_on_cancel_and_error(stubs, monkeypatch):
    import server.customer_gateway.relay as relay_module

    # The per-user cap stays full for the whole test (the first held request's
    # slot is only ever released explicitly below), so the second call's
    # capacity wait is deliberately unmet -- shorten it so the test stays fast.
    monkeypatch.setattr(relay_module, 'CAPACITY_WAIT_S', 0.05)

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                yield b'data: {"choices":[]}\n\n'

    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 3:
            return httpx.Response(500, stream=httpx.ByteStream(b'{"error":"x"}'))
        return httpx.Response(
            200, stream=Chunks(), headers={'content-type': 'text/event-stream'}
        )

    relay, _ = _relay(handler, max_inflight=64, max_inflight_per_user=1)
    held = await relay.chat_completions(_request({**BODY, 'stream': True}))
    assert held.status_code == 200 and relay._user_inflight == {
        SB['created_by_user_id']: 1
    }
    second = await relay.chat_completions(_request({**BODY, 'stream': True}))
    assert second.status_code == 429 and len(calls) == 1

    async def send(message):
        raise OSError('client went away')

    async def receive():
        return {'type': 'http.disconnect'}

    with pytest.raises((OSError, ClientDisconnect)):
        await held({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    assert relay._inflight == 0 and relay._user_inflight == {}

    again = await relay.chat_completions(_request({**BODY, 'stream': True}))
    assert again.status_code == 200
    with pytest.raises((OSError, ClientDisconnect)):
        await again({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    failed = await relay.chat_completions(_request())
    assert failed.status_code == 500
    assert relay._inflight == 0 and relay._user_inflight == {}


async def test_queued_reauth_deadline_expiry_returns_gateway_timeout_and_frees_slot(
    stubs, monkeypatch
):
    """Real expiry of the post-wait reauthorization deadline (CAPACITY_REAUTH_TIMEOUT_S)
    returns llm_gateway_authorization_timeout, not sandbox_status_unknown, with no
    upstream call and the capacity slot released."""
    import server.customer_gateway.relay as relay_module

    monkeypatch.setattr(relay_module, 'CAPACITY_WAIT_S', 1.0)
    monkeypatch.setattr(relay_module, 'CAPACITY_REAUTH_TIMEOUT_S', 0.02)

    calls = 0

    async def get_sandbox_fast_then_slow(*a, **kw):
        nonlocal calls
        calls += 1
        if calls > 1:
            # Only the post-wait reauthorization call is slow, so the request
            # actually takes the wait/reauth path.
            await anyio.sleep(0.2)  # longer than CAPACITY_REAUTH_TIMEOUT_S
        return SimpleNamespace(**SB)

    relay, seen = _relay(_ok, max_inflight=1)
    held_release = relay._acquire('someone-else')
    assert held_release is not None

    async def release_soon():
        await anyio.sleep(0.05)
        held_release()

    task = asyncio.create_task(release_soon())
    try:
        with patch.object(
            service, 'get_sandbox', AsyncMock(side_effect=get_sandbox_fast_then_slow)
        ):
            resp = await relay.chat_completions(_request())
    finally:
        await task

    assert resp.status_code == 503
    assert seen == []  # upstream never attempted
    body = json.loads(resp.body)
    assert body['error']['code'] == 'llm_gateway_authorization_timeout'
    assert body['error']['message'] == AUTHORIZATION_TIMEOUT_MESSAGE
    assert resp.headers['retry-after'] == '1'
    assert relay._inflight == 0  # the capacity slot was released
    assert relay._waiting == 0


@pytest.mark.parametrize('route', ['chat_completions', 'models'])
async def test_sandbox_status_unknown_denial_wording(stubs, route):
    """CustomerGatewaySandboxLookupUnavailable returns sandbox_status_unknown with
    its literal user-facing message, on both relay endpoints."""
    from server.customer_gateway.service import CustomerGatewaySandboxLookupUnavailable

    relay, seen = _relay(_ok)
    with patch.object(
        relay,
        '_authorize',
        AsyncMock(side_effect=CustomerGatewaySandboxLookupUnavailable()),
    ):
        resp = await getattr(relay, route)(_request())

    assert resp.status_code == 503
    assert seen == []
    body = json.loads(resp.body)
    assert body['error']['code'] == 'sandbox_status_unknown'
    assert body['error']['message'] == (
        'OpenHands could not confirm the sandbox status while authorizing the '
        'LLM gateway request. The request was not sent to your LLM provider.'
    )
    assert resp.headers['retry-after'] == '1'


@pytest.mark.parametrize('timeout', [None, 5.0])
async def test_unrelated_timeout_error_propagates(stubs, timeout):
    """A TimeoutError raised by _authorize itself (not the relay's own deadline)
    must propagate unhandled, both with no active deadline (initial
    authorization) and with one active but unexpired (post-wait reauth)."""
    relay, seen = _relay(_ok)
    with patch.object(relay, '_authorize', AsyncMock(side_effect=TimeoutError('boom'))):
        with pytest.raises(TimeoutError, match='boom'):
            await relay._authorize_or_deny(_request(), 'rid', timeout=timeout)
    assert seen == []
