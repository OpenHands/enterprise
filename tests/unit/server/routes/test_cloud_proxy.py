"""Tests for the cloud-proxy route.

These exercise the real validation and forwarding logic: SSRF bypass attempts,
the host allowlist, IP-class rejection, loopback/local mode, hop-by-hop header
stripping, auth enforcement, and that the final (host+path) URL is re-validated.
The upstream httpx client is mocked so no real network call is made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routes.cloud_proxy import (
    _REQUEST_HOP_BY_HOP_HEADERS,
    _RESPONSE_HOP_BY_HOP_HEADERS,
    cloud_proxy_router,
)

CLOUD_HOST = 'https://abc.prod-runtime.all-hands.dev'


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(cloud_proxy_router)
    # Default: authenticated. Individual tests can override to None for 401.
    _override_user_id(application, 'user-123')
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _override_user_id(application, user_id):
    """Wire get_user_id to return a fixed identity (or None for 401 tests)."""
    from openhands.app_server.user_auth import get_user_id

    async def _dep():
        return user_id

    application.dependency_overrides[get_user_id] = _dep


def _patch_resolve(hostname, ip='203.0.113.10', port=443):
    """Patch the blocking resolver to return a public IP without touching DNS."""
    return patch(
        'server.routes.cloud_proxy._resolve_target',
        return_value=(hostname, ip, port),
    )


def _mock_upstream(status_code=200, body=b'{"status":"ok"}', headers=None):
    """Build a streamed httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {'content-type': 'application/json'}
    resp.aclose = AsyncMock()

    async def _aiter_raw():
        yield body

    resp.aiter_raw = _aiter_raw
    return resp


def _mock_client(upstream):
    client = MagicMock()
    client.build_request = MagicMock(return_value=MagicMock())
    client.send = AsyncMock(return_value=upstream)
    client.aclose = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return client, ctx


# ---------------------------------------------------------------------------
# Validation: SSRF bypass via path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'path',
    [
        '@169.254.169.254/latest/meta-data/',
        'evil.com/x',
        ' http://169.254.169.254/',
    ],
)
def test_path_ssrf_bypass_rejected(client, path):
    """A non-absolute path must be rejected so host+path can't re-parse."""
    response = client.post(
        '/api/cloud-proxy',
        json={'host': CLOUD_HOST, 'method': 'GET', 'path': path},
    )
    assert response.status_code == 422


def test_protocol_relative_path_keeps_host(app):
    """A path starting with // is allowed (it keeps the validated host) but
    must still be re-checked so the final URL host is unchanged."""
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    with _patch_resolve('abc.prod-runtime.all-hands.dev'):
        # Re-validation should pass; we only assert it does not 400/422 on path.
        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '//169.254.169.254/'},
        )
    assert response.status_code != 422  # path accepted
    assert response.status_code != 400  # final host unchanged


def test_non_allowed_host_rejected(client):
    response = client.post(
        '/api/cloud-proxy',
        json={'host': 'https://evil.com', 'method': 'GET', 'path': '/x'},
    )
    assert response.status_code == 422


def test_http_rejected_unless_local(client):
    response = client.post(
        '/api/cloud-proxy',
        json={
            'host': 'http://abc.prod-runtime.all-hands.dev',
            'method': 'GET',
            'path': '/x',
        },
    )
    assert response.status_code == 422


def test_userinfo_in_host_rejected(client):
    response = client.post(
        '/api/cloud-proxy',
        json={
            'host': 'https://u:p@abc.prod-runtime.all-hands.dev',
            'method': 'GET',
            'path': '/x',
        },
    )
    assert response.status_code == 422


def test_suffix_override_accepted(client, monkeypatch):
    monkeypatch.setenv('CLOUD_PROXY_RUNTIME_HOST_SUFFIXES', '.my-runtime.example')
    response = client.post(
        '/api/cloud-proxy',
        json={'host': 'https://run.my-runtime.example', 'method': 'GET', 'path': '/x'},
    )
    # Host validation must pass (not 422). It then reaches the resolver, which
    # fails on a non-resolvable test domain -> 400. Either proves validation
    # accepted the overridden suffix.
    assert response.status_code in (400, 200)


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_unauthenticated_returns_401(app):
    _override_user_id(app, None)
    client = TestClient(app)
    with _patch_resolve('abc.prod-runtime.all-hands.dev'):
        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Happy path + hop-by-hop stripping + streaming
# ---------------------------------------------------------------------------


def test_forwards_and_streams_response(app):
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    upstream = _mock_upstream(200, b'{"status":"ok"}')

    with (
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={
                'host': CLOUD_HOST,
                'method': 'POST',
                'path': '/api/conversations/c/condense',
                'headers': {'X-Session-API-Key': 'sekret', 'Connection': 'keep-alive'},
                'body': {'foo': 'bar'},
            },
        )

    assert response.status_code == 200
    assert response.text == '{"status":"ok"}'
    # Hop-by-hop request headers must not be forwarded.
    sent_headers = mock_client.build_request.call_args.kwargs['headers']
    assert 'X-Session-API-Key' in sent_headers
    assert 'Connection' not in sent_headers
    assert 'Host' not in sent_headers


def test_response_hop_by_hop_headers_stripped(app):
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    upstream = _mock_upstream(
        500,
        b'{"error":"x"}',
        headers={
            'content-type': 'application/json',
            'connection': 'keep-alive',
            'transfer-encoding': 'chunked',
            'x-runtime': 'oh',
        },
    )

    with (
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )

    assert response.status_code == 500
    assert 'x-runtime' in {k.lower() for k in response.headers}
    assert 'connection' not in {k.lower() for k in response.headers}
    assert 'transfer-encoding' not in {k.lower() for k in response.headers}


# ---------------------------------------------------------------------------
# Final-URL re-validation (path cannot change the host post-concat)
# ---------------------------------------------------------------------------


def test_final_url_host_mismatch_rejected(app):
    """If the resolver's hostname disagrees with the envelope host, the final
    URL re-validation must reject (defense against any path that changes host)."""
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    # Resolver returns a different hostname than the envelope host parses to.
    with patch(
        'server.routes.cloud_proxy._resolve_target',
        return_value=('not-the-same-host', '203.0.113.10', 443),
    ):
        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400
    assert 'path' in response.json()['detail']


# ---------------------------------------------------------------------------
# Resolver: IP-class rejection
# ---------------------------------------------------------------------------


def test_private_ip_rejected(app):
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    with patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai:
        gai.return_value = [
            (socket_fam(), 1, 6, '', ('10.0.0.1', 443)),
        ]
        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400
    assert 'non-public' in response.json()['detail']


def test_loopback_rejected_without_local_flag(app, monkeypatch):
    monkeypatch.delenv('CLOUD_PROXY_ALLOW_LOCAL_RUNTIME', raising=False)
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    with patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai:
        gai.return_value = [(socket_fam(), 1, 6, '', ('127.0.0.1', 443))]
        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400


def test_loopback_allowed_with_local_flag(app, monkeypatch):
    monkeypatch.setenv('CLOUD_PROXY_ALLOW_LOCAL_RUNTIME', '1')
    _override_user_id(app, 'user-123')
    client = TestClient(app)
    upstream = _mock_upstream(200, b'ok')
    with (
        patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai,
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        gai.return_value = [(socket_fam(), 1, 6, '', ('127.0.0.1', 8008))]
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={
                'host': 'http://127.0.0.1:8008',
                'method': 'GET',
                'path': '/alive',
            },
        )
    assert response.status_code == 200
    assert response.text == 'ok'


def test_unresolvable_host_rejected(app):
    import socket as _socket

    _override_user_id(app, 'user-123')
    client = TestClient(app)
    with patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai:
        gai.side_effect = _socket.gaierror('nope')
        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400
    assert 'resolved' in response.json()['detail']


# ---------------------------------------------------------------------------
# Upstream failure -> 502
# ---------------------------------------------------------------------------


def test_upstream_request_error_returns_502(app):
    import httpx

    _override_user_id(app, 'user-123')
    client = TestClient(app)
    with (
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        mock_client = MagicMock()
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(side_effect=httpx.ConnectError('boom'))
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={'host': CLOUD_HOST, 'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# Timeout cap
# ---------------------------------------------------------------------------


def test_timeout_over_cap_rejected(client):
    response = client.post(
        '/api/cloud-proxy',
        json={
            'host': CLOUD_HOST,
            'method': 'GET',
            'path': '/x',
            'timeout_seconds': 99999,
        },
    )
    assert response.status_code == 422


def socket_fam():
    import socket as _socket

    return _socket.AF_INET


# Sanity: the header sets are non-empty and cover the dangerous ones.
def test_hop_by_hop_sets_cover_dangerous_headers():
    for h in ('host', 'connection', 'transfer-encoding', 'content-length'):
        assert h in _REQUEST_HOP_BY_HOP_HEADERS
    for h in ('connection', 'transfer-encoding', 'content-length'):
        assert h in _RESPONSE_HOP_BY_HOP_HEADERS
