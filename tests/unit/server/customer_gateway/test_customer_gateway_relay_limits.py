"""Relay bounds on the native mount: capacity, body size, redirects, error and cancel release.

Same authorizer and stubs as test_customer_gateway_relay.py; upstream is an httpx MockTransport."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.requests import ClientDisconnect, Request

from server.customer_gateway import service
from server.customer_gateway.relay import InferenceRelay, RelayConfig

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


async def test_capacity_429_without_upstream(stubs):
    """Persistent fill: capacity never recovers, so the single bounded wait still
    ends in a 429 with no upstream call, and no additional wait/queueing happens."""
    relay, seen = _relay(_ok, max_inflight=1)
    relay._inflight = 1
    resp = await relay.chat_completions(_request())
    assert resp.status_code == 429 and seen == []


async def test_capacity_recovers_during_bounded_wait(stubs, monkeypatch):
    """Capacity freed while the relay is in its single short wait: the request
    proceeds (one upstream call), it isn't denied, and the wait is bounded (one
    `anyio.sleep` call, not a queue of retries)."""
    import server.customer_gateway.relay as relay_module

    relay, seen = _relay(_ok, max_inflight=1)
    relay._inflight = 1
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        assert 0 < seconds <= 0.5
        relay._inflight = 0  # capacity recovers while we wait

    monkeypatch.setattr(relay_module.anyio, 'sleep', fake_sleep)
    resp = await relay.chat_completions(_request())
    assert resp.status_code == 200
    assert len(seen) == 1
    assert sleep_calls == [relay_module.CAPACITY_WAIT_S]
    assert relay._waiting == 0


async def test_capacity_wait_reauthorizes_and_denies_stale_policy(stubs, monkeypatch):
    """A member resolution that goes stale (e.g. a rotated/removed model)
    while waiting for capacity must not reach upstream: reauthorization runs
    before the second `_acquire`, and the response reflects the fresh denial
    rather than the pre-wait snapshot."""
    from server.customer_gateway import service

    relay, seen = _relay(_ok, max_inflight=1)
    relay._inflight = 1

    stale_res = SimpleNamespace(**{**RES.__dict__, 'allowed_models': frozenset()})

    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        relay._inflight = 0  # capacity recovers, but policy changed underneath

    monkeypatch.setattr('server.customer_gateway.relay.anyio.sleep', fake_sleep)
    with patch.object(
        service.CustomerGatewayStore, 'resolve', AsyncMock(return_value=stale_res)
    ):
        resp = await relay.chat_completions(_request())

    assert resp.status_code == 403
    assert seen == []  # upstream never attempted
    assert call_count == 1
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


async def test_per_user_cap_and_release_on_cancel_and_error(stubs):
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
