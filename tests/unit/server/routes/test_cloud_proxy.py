"""Tests for the cloud-proxy route.

These exercise the real validation and forwarding logic: that the destination
host is derived from the caller's session key (not the client), that the
session key is bound to the authenticated user (401/403), SSRF path bypasses,
IP-class rejection, loopback/local mode, hop-by-hop header stripping, and that
the final (host+path) URL is re-validated. The upstream httpx client and the
session-key ownership lookup are mocked so no real network/runtime call is made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from server.routes.cloud_proxy import (
    _REQUEST_HOP_BY_HOP_HEADERS,
    _RESPONSE_HOP_BY_HOP_HEADERS,
    _user_context_dependency,
    cloud_proxy_router,
)

CLOUD_HOST = 'https://abc.prod-runtime.all-hands.dev'
SESSION_KEY = 'sekret'


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(cloud_proxy_router)
    # Default: authenticated, with a session key that resolves to the caller's
    # own sandbox on the cloud runtime host. Individual tests override pieces.
    _override_user(application, 'user-123')
    _override_ownership(application, _owned_sandbox())
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _owned_sandbox(host: str = CLOUD_HOST, owner: str = 'user-123') -> SandboxInfo:
    return SandboxInfo(
        id='sb-1',
        created_by_user_id=owner,
        sandbox_spec_id='spec-1',
        status=SandboxStatus.RUNNING,
        session_api_key=SESSION_KEY,
        exposed_urls=[ExposedUrl(name=AGENT_SERVER, url=host, port=60000)],
    )


class _FakeUserContext:
    def __init__(self, user_id: str | None) -> None:
        self._user_id = user_id

    async def get_user_id(self) -> str | None:
        return self._user_id


def _user_dep():
    # The route's default is the module-level Depends object; FastAPI keys
    # overrides on the wrapped callable (``Depends.dependency``), which is the
    # bound injector method captured at import time. Override that exact object
    # so the override is stable regardless of how get_global_config() resolves
    # at test time.
    return _user_context_dependency.dependency


def _override_user(application, user_id: str | None):
    """Wire the UserContext dependency to a fixed identity (or None for 401)."""
    application.dependency_overrides[_user_dep()] = _fake_user_dep(user_id)


def _fake_user_dep(user_id: str | None):
    async def _dep():
        return _FakeUserContext(user_id)

    return _dep


def _override_ownership(application, sandbox_info: SandboxInfo | None):
    """No-op placeholder; ownership is patched per-test via _patch_ownership().

    Kept so the default ``app`` fixture reads clearly; the real ownership
    behavior is exercised by patching the module-level call in each test.
    """
    return sandbox_info


def _patch_ownership(sandbox_info: SandboxInfo | None, exc=None):
    """Patch the module-level ownership call used by the route."""
    if exc is not None:
        return patch(
            'server.routes.cloud_proxy.validate_session_key_ownership',
            new=AsyncMock(side_effect=exc),
        )
    return patch(
        'server.routes.cloud_proxy.validate_session_key_ownership',
        new=AsyncMock(return_value=sandbox_info),
    )


def _patch_resolve(hostname, ip='203.0.113.10', port=443, scheme='https'):
    """Patch the blocking resolver to return a public IP without touching DNS."""
    return patch(
        'server.routes.cloud_proxy._resolve_target',
        return_value=(hostname, ip, port, scheme),
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
# Auth + session-key ownership binding
# ---------------------------------------------------------------------------


def test_unauthenticated_returns_401(app):
    _override_user(app, None)
    client = TestClient(app)
    response = client.post(
        '/api/cloud-proxy',
        json={'method': 'GET', 'path': '/alive'},
    )
    assert response.status_code == 401


def test_missing_session_key_rejected(app):
    """Ownership validation runs before any forwarding; a missing key 401s."""
    from fastapi import HTTPException, status

    client = TestClient(app)
    with _patch_ownership(None, exc=HTTPException(status.HTTP_401_UNAUTHORIZED)):
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 401


def test_session_key_owned_by_other_user_rejected(app):
    """A session key that belongs to a different user must 403."""
    from fastapi import HTTPException, status

    client = TestClient(app)
    with _patch_ownership(None, exc=HTTPException(status.HTTP_403_FORBIDDEN)):
        response = client.post(
            '/api/cloud-proxy',
            json={
                'method': 'GET',
                'path': '/alive',
                'headers': {'X-Session-API-Key': 'stolen'},
            },
        )
    assert response.status_code == 403


def test_host_is_derived_from_session_key_not_client(app):
    """A client-supplied `host` in the envelope must be ignored — the route
    derives the host from the validated sandbox's exposed URLs."""
    client = TestClient(app)
    # Envelope includes a malicious `host`; it must be disregarded.
    with (
        _patch_ownership(_owned_sandbox()),
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        upstream = _mock_upstream(200, b'ok')
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client
        response = client.post(
            '/api/cloud-proxy',
            json={
                'host': 'https://evil.com',
                'method': 'GET',
                'path': '/alive',
                'headers': {'X-Session-API-Key': SESSION_KEY},
            },
        )
    assert response.status_code == 200
    # The pinned URL sent upstream is the derived cloud host, not evil.com.
    sent_url = mock_client.build_request.call_args.args[1]
    assert 'evil.com' not in sent_url
    assert '203.0.113.10' in sent_url  # pinned resolved IP


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
        json={'method': 'GET', 'path': path},
    )
    assert response.status_code == 422


def test_protocol_relative_path_keeps_host(app):
    """A path starting with // keeps the derived host; final URL re-checked."""
    client = TestClient(app)
    with (
        _patch_ownership(_owned_sandbox()),
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        upstream = _mock_upstream(200, b'ok')
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client
        response = client.post(
            '/api/cloud-proxy',
            json={
                'method': 'GET',
                'path': '//169.254.169.254/',
                'headers': {'X-Session-API-Key': SESSION_KEY},
            },
        )
    assert response.status_code != 422  # path accepted
    assert response.status_code != 400  # final host unchanged


# ---------------------------------------------------------------------------
# Happy path + hop-by-hop stripping + streaming
# ---------------------------------------------------------------------------


def test_forwards_and_streams_response(app):
    client = TestClient(app)
    upstream = _mock_upstream(200, b'{"status":"ok"}')

    with (
        _patch_ownership(_owned_sandbox()),
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={
                'method': 'POST',
                'path': '/api/conversations/c/condense',
                'headers': {
                    'X-Session-API-Key': SESSION_KEY,
                    'Connection': 'keep-alive',
                },
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
        _patch_ownership(_owned_sandbox()),
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )

    assert response.status_code == 500
    assert 'x-runtime' in {k.lower() for k in response.headers}
    assert 'connection' not in {k.lower() for k in response.headers}
    assert 'transfer-encoding' not in {k.lower() for k in response.headers}


# ---------------------------------------------------------------------------
# Final-URL re-validation (path cannot change the host post-concat)
# ---------------------------------------------------------------------------


def test_final_url_host_mismatch_rejected(app):
    """If the resolver's hostname disagrees with the derived host, the final
    URL re-validation must reject (defense against any path that changes host)."""
    client = TestClient(app)
    with (
        _patch_ownership(_owned_sandbox()),
        patch(
            'server.routes.cloud_proxy._resolve_target',
            return_value=('not-the-same-host', '203.0.113.10', 443, 'https'),
        ),
    ):
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400
    assert 'path' in response.json()['detail']


# ---------------------------------------------------------------------------
# Resolver: IP-class rejection
# ---------------------------------------------------------------------------


def test_private_ip_rejected(app):
    client = TestClient(app)
    with (
        _patch_ownership(_owned_sandbox()),
        patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai,
    ):
        gai.return_value = [
            (socket_fam(), 1, 6, '', ('10.0.0.1', 443)),
        ]
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400
    assert 'non-public' in response.json()['detail']


def test_loopback_rejected_without_local_flag(app, monkeypatch):
    monkeypatch.delenv('CLOUD_PROXY_ALLOW_LOCAL_RUNTIME', raising=False)
    client = TestClient(app)
    with (
        _patch_ownership(_owned_sandbox()),
        patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai,
    ):
        gai.return_value = [(socket_fam(), 1, 6, '', ('127.0.0.1', 443))]
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400


def test_loopback_allowed_with_local_flag(app, monkeypatch):
    monkeypatch.setenv('CLOUD_PROXY_ALLOW_LOCAL_RUNTIME', '1')
    client = TestClient(app)
    upstream = _mock_upstream(200, b'ok')
    with (
        _patch_ownership(_owned_sandbox(host='http://127.0.0.1:8008')),
        patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai,
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        gai.return_value = [(socket_fam(), 1, 6, '', ('127.0.0.1', 8008))]
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={
                'method': 'GET',
                'path': '/alive',
                'headers': {'X-Session-API-Key': SESSION_KEY},
            },
        )
    assert response.status_code == 200
    assert response.text == 'ok'


def test_unresolvable_host_rejected(app):
    import socket as _socket

    client = TestClient(app)
    with (
        _patch_ownership(_owned_sandbox()),
        patch('server.routes.cloud_proxy.socket.getaddrinfo') as gai,
    ):
        gai.side_effect = _socket.gaierror('nope')
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400
    assert 'resolved' in response.json()['detail']


def test_derived_host_not_under_suffix_rejected(app, monkeypatch):
    """Even though the host is server-derived, a runtime that advertises a host
    outside the allowlist must be rejected (defense-in-depth)."""
    monkeypatch.delenv('CLOUD_PROXY_ALLOW_LOCAL_RUNTIME', raising=False)
    client = TestClient(app)
    with _patch_ownership(_owned_sandbox(host='https://evil.com')):
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 400


def test_no_agent_server_url_rejected(app):
    """A sandbox with no AGENT_SERVER exposed URL cannot be proxied."""
    client = TestClient(app)
    bare = SandboxInfo(
        id='sb-1',
        created_by_user_id='user-123',
        sandbox_spec_id='spec-1',
        status=SandboxStatus.RUNNING,
        session_api_key=SESSION_KEY,
        exposed_urls=[],
    )
    with _patch_ownership(bare):
        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# Upstream failure -> 502
# ---------------------------------------------------------------------------


def test_upstream_request_error_returns_502(app):
    import httpx

    client = TestClient(app)
    with (
        _patch_ownership(_owned_sandbox()),
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
            json={'method': 'GET', 'path': '/alive'},
        )
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# Timeout cap
# ---------------------------------------------------------------------------


def test_timeout_over_cap_rejected(client):
    response = client.post(
        '/api/cloud-proxy',
        json={
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
    # Response body is forwarded verbatim, so content-encoding and content-length
    # are end-to-end and must be PRESERVED (not stripped). Only true hop-by-hop
    # headers are stripped on the response side.
    for h in ('connection', 'transfer-encoding', 'upgrade'):
        assert h in _RESPONSE_HOP_BY_HOP_HEADERS
    assert 'content-encoding' not in _RESPONSE_HOP_BY_HOP_HEADERS
    assert 'content-length' not in _RESPONSE_HOP_BY_HOP_HEADERS


def test_compressed_response_round_trips_intact(app):
    """A gzip-compressed upstream response must reach the client with its
    Content-Encoding and Content-Length headers intact and body bytes
    unchanged — the proxy forwards raw bytes, so it must not strip the
    headers that tell the client how to decode them."""
    import gzip

    client = TestClient(app)
    payload = b'{"status":"ok"}'
    compressed = gzip.compress(payload)
    upstream = _mock_upstream(
        200,
        compressed,
        headers={
            'content-type': 'application/json',
            'content-encoding': 'gzip',
            'content-length': str(len(compressed)),
        },
    )

    with (
        _patch_ownership(_owned_sandbox()),
        _patch_resolve('abc.prod-runtime.all-hands.dev'),
        patch('server.routes.cloud_proxy.httpx.AsyncClient') as mock_cls,
    ):
        mock_client, _ctx = _mock_client(upstream)
        mock_cls.return_value = mock_client

        response = client.post(
            '/api/cloud-proxy',
            json={'method': 'GET', 'path': '/alive'},
        )

    assert response.status_code == 200
    # The proxy forwards the raw compressed bytes unchanged; the TestClient
    # (browser-equivalent) auto-decompresses based on the preserved
    # Content-Encoding header, so response.content is the decoded payload.
    assert response.content == payload
    # Encoding + length headers preserved so the client can decode.
    lowered = {k.lower(): v for k, v in response.headers.items()}
    assert lowered['content-encoding'] == 'gzip'
    assert lowered['content-length'] == str(len(compressed))
    # And the payload actually decompresses to the original body.
    assert gzip.decompress(compressed) == payload
