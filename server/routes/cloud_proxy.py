"""Authenticated proxy for browser requests to OpenHands runtime hosts.

Cloud Canvas cannot call a per-conversation runtime directly from the browser:
the runtime host is not the same origin as the Canvas and may not expose CORS
headers. The Canvas client therefore sends a request envelope to this backend,
which forwards it to the runtime after validating the destination.

The destination host is NOT trusted from the client. It is derived from the
caller's session API key: the key is validated, bound to the authenticated
user (``validate_session_key_ownership``), and the runtime host is read from
the resulting ``SandboxInfo.exposed_urls``. The client therefore cannot point
this proxy at an arbitrary host — only at the runtime that issued the key it
already owns.

SSRF hardening: the derived host is still validated against the configured
runtime host suffixes, resolved once, and the resolved IP is pinned for the
actual connection (with the original hostname preserved for TLS SNI / cert
validation). The *final* request URL is re-validated after the host and path
are combined, so a crafted ``path`` cannot redirect the connection past the
allowlist. Redirects are not followed and transport-proxy env vars are ignored.

Self-hosted or local deployments that run runtimes on loopback can opt in with
``CLOUD_PROXY_ALLOW_LOCAL_RUNTIME=1``; this is dev-only and must never be
enabled in production.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from collections.abc import AsyncIterator
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from openhands.app_server.config import depends_user_context
from openhands.app_server.sandbox.sandbox_models import AGENT_SERVER, SandboxInfo
from openhands.app_server.sandbox.session_auth import validate_session_key_ownership
from openhands.app_server.user.user_context import UserContext
from server.logger import logger

_DEFAULT_RUNTIME_HOST_SUFFIXES = (
    '.prod-runtime.all-hands.dev',
    '.staging-runtime.all-hands.dev',
)

# Upper bound on the caller-supplied timeout so a single request cannot hold a
# backend->runtime connection open indefinitely.
_MAX_TIMEOUT_SECONDS = 60.0
_DEFAULT_TIMEOUT_SECONDS = 30.0

_SESSION_API_KEY_HEADER = 'X-Session-API-Key'

# Shared across requests so TCP connections and TLS sessions are reused. The
# per-request timeout is set via build_request(timeout=...), not here. Must be
# closed on application shutdown (see close_cloud_proxy_client).
_cloud_proxy_client: httpx.AsyncClient | None = None


def get_cloud_proxy_client() -> httpx.AsyncClient:
    """Lazily build a shared, pooled httpx client.

    ``trust_env=False`` and ``follow_redirects=False`` are set here so no
    request can be redirected or routed through a transport-proxy env var.
    """
    global _cloud_proxy_client
    if _cloud_proxy_client is None or _cloud_proxy_client.is_closed:
        _cloud_proxy_client = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
        )
    return _cloud_proxy_client


async def close_cloud_proxy_client() -> None:
    """Close the shared client (call on application shutdown)."""
    global _cloud_proxy_client
    if _cloud_proxy_client is not None and not _cloud_proxy_client.is_closed:
        await _cloud_proxy_client.aclose()
    _cloud_proxy_client = None


def _runtime_host_suffixes() -> tuple[str, ...]:
    """Allowed runtime host suffixes, overridable for self-hosted deployments."""
    override = os.environ.get('CLOUD_PROXY_RUNTIME_HOST_SUFFIXES', '').strip()
    if not override:
        return _DEFAULT_RUNTIME_HOST_SUFFIXES
    return tuple(s.strip() for s in override.split(',') if s.strip())


def _allow_local_runtime() -> bool:
    """Whether loopback/private http runtime hosts are accepted (dev only)."""
    return os.environ.get('CLOUD_PROXY_ALLOW_LOCAL_RUNTIME', '').strip() in (
        '1',
        'true',
        'True',
    )


_REQUEST_HOP_BY_HOP_HEADERS = frozenset(
    {
        'connection',
        'content-length',
        'content-encoding',
        'host',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailer',
        'transfer-encoding',
        'upgrade',
    }
)

# Response body is forwarded verbatim (upstream.aiter_raw), so the headers that
# describe that body — content-encoding and content-length — are end-to-end and
# must be preserved. Stripping content-encoding sends compressed bytes the client
# cannot decode; stripping content-length while also dropping transfer-encoding
# leaves the client without a framing/length signal. Only true hop-by-hop headers
# (RFC 9110 §7.6.1) are stripped here.
_RESPONSE_HOP_BY_HOP_HEADERS = frozenset(
    {
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailer',
        'transfer-encoding',
        'upgrade',
    }
)


class CloudProxyRequest(BaseModel):
    """Request envelope for forwarding a browser call to a runtime host.

    The upstream host is intentionally absent: it is derived server-side from
    the caller's session API key (see ``proxy_cloud_request``), so the client
    cannot choose the destination.
    """

    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
    path: str = Field(
        description='Upstream absolute path (must start with /), including query string'
    )
    headers: dict[str, str] = Field(default_factory=dict)
    # Text body forwarded verbatim (httpx UTF-8-encodes the str onto the wire).
    # The caller supplies Content-Type via ``headers`` so text bodies (JSON,
    # form-urlencoded, text/plain) are carried faithfully instead of being
    # JSON-encoded and stamped application/json. Binary bodies are NOT
    # supported: a JSON envelope cannot carry non-UTF-8 bytes. If binary
    # passthrough is later needed, add an explicit base64 field (or a
    # non-JSON envelope) rather than widening this to ``bytes``.
    body: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=_MAX_TIMEOUT_SECONDS)

    @field_validator('path')
    @classmethod
    def validate_path(cls, value: str) -> str:
        # A non-"/"-prefixed path turns the derived host into RFC userinfo
        # when concatenated (e.g. path="@169.254.169.254/...") and redirects
        # the actual connection past the host allowlist. Require an absolute
        # path so host+path cannot re-parse to a different host.
        if not value.startswith('/'):
            raise ValueError('path must be an absolute path starting with /')
        return value


def _derive_runtime_host(sandbox_info: SandboxInfo) -> str:
    """Return the AGENT_SERVER base URL from the validated sandbox.

    The host comes from the runtime's own advertised exposed URLs, not from
    the client. It is still run through ``_resolve_target`` (suffix allowlist
    + IP-class rejection + pinning) before any connection is made.
    """
    for exposed in sandbox_info.exposed_urls or []:
        if exposed.name == AGENT_SERVER:
            return exposed.url
    raise HTTPException(
        status_code=502,
        detail='runtime has no reachable agent-server URL',
    )


cloud_proxy_router = APIRouter(prefix='/api/cloud-proxy', tags=['Cloud Proxy'])

# Module-level dependency so it isn't re-evaluated on every request (B008) and
# can be overridden in tests via application.dependency_overrides.
_user_context_dependency = depends_user_context()


def _validate_host_form(host: str) -> str:
    """Validate the scheme/hostname form of a server-derived runtime host.

    The host is derived from the runtime's exposed URLs (not from the client),
    so this is defense-in-depth: it asserts the derived value is an ``https``
    URL (``http`` only in local mode) whose hostname is under a configured
    runtime suffix or — in local mode only — a loopback/localhost address.
    Returns the normalized ``scheme://hostname[:port]`` base.
    """
    parsed = urlparse(host)
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail='runtime host has an invalid port'
        ) from exc

    allow_local = _allow_local_runtime()
    allowed_schemes = ('https', 'http') if allow_local else ('https',)

    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=400, detail='runtime host is not allowed')

    hostname = parsed.hostname.lower().rstrip('.')
    suffixes = _runtime_host_suffixes()
    host_allowed = any(hostname.endswith(s) for s in suffixes)

    # In local-runtime mode, loopback hosts on any port are accepted so a
    # self-hosted Canvas can forward to a co-located agent-server runtime.
    if allow_local and not host_allowed:
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None
        host_allowed = (ip is not None and ip.is_loopback) or hostname == 'localhost'

    if not host_allowed:
        raise HTTPException(status_code=400, detail='runtime host is not allowed')

    if port and allow_local:
        return f'{"http" if parsed.scheme == "http" else "https"}://{hostname}:{port}'
    return f'{parsed.scheme}://{hostname}'


def _resolve_target(host: str) -> tuple[str, str, int, str]:
    """Resolve the host and return (hostname, pinned_ip, port, scheme).

    Rejects non-public addresses unless local mode is on. The returned IP is
    pinned for the actual connection so a DNS record cannot rebind to a
    private address between the check and the connect.
    """
    base = _validate_host_form(host)
    parsed = urlparse(base)
    hostname = parsed.hostname
    assert hostname is not None  # _validate_host_form guarantees a hostname
    allow_local = _allow_local_runtime()
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)

    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=400, detail='host could not be resolved'
        ) from exc

    if not addresses:
        raise HTTPException(status_code=400, detail='host could not be resolved')

    for *_, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            if allow_local and ip.is_loopback:
                continue
            raise HTTPException(
                status_code=400,
                detail='host resolves to a non-public address',
            )

    # All resolved addresses passed the filter; connect to the first one.
    return hostname, str(addresses[0][4][0]), port, parsed.scheme


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep end-to-end headers while letting httpx own transport headers."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _REQUEST_HOP_BY_HOP_HEADERS
    }


@cloud_proxy_router.post(
    '',
    response_class=StreamingResponse,
    responses={200: {'description': 'Upstream response'}},
)
async def proxy_cloud_request(
    envelope: CloudProxyRequest,
    user_context: UserContext = _user_context_dependency,
) -> StreamingResponse:
    """Forward an authenticated request to the caller's own OpenHands runtime.

    The destination host is derived from the ``X-Session-API-Key`` the client
    supplies: the key is validated and bound to the authenticated user, then
    the runtime host is read from the resulting sandbox's exposed URLs. The
    client never chooses the host, so this cannot be aimed at an arbitrary
    target.
    """
    caller_id = await user_context.get_user_id()
    if not caller_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    session_api_key = envelope.headers.get(_SESSION_API_KEY_HEADER)
    # Bind the session key to the caller: 401 if missing/invalid/not running,
    # 403 if it belongs to a different user. Returns the sandbox we derive the
    # runtime host from.
    sandbox_info = await validate_session_key_ownership(user_context, session_api_key)
    host = _derive_runtime_host(sandbox_info)

    hostname, ip, port, scheme = await asyncio.to_thread(_resolve_target, host)

    # Re-validate the *final* URL: combine host + path and assert the hostname
    # is unchanged, so a crafted path cannot redirect the connection.
    final_url = f'{host}{envelope.path}'
    final_host = urlparse(final_url).hostname
    if not final_host or final_host.lower().rstrip('.') != hostname:
        raise HTTPException(status_code=400, detail='invalid request path')

    # Pin the resolved IP in the URL while preserving the original hostname for
    # TLS SNI / certificate validation via the sni_hostname extension. This
    # closes the DNS-rebinding window between resolve and connect.
    port_suffix = f':{port}' if port not in (443, 80) else ''
    ip_host = f'[{ip}]' if ':' in ip else ip  # bracket IPv6 literals
    pinned_url = f'{scheme}://{ip_host}{port_suffix}{envelope.path}'
    extensions = {'sni_hostname': hostname} if scheme == 'https' else {}

    timeout = envelope.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS
    logger.debug(
        'cloud_proxy forwarding %s %s (user=%s)',
        envelope.method,
        final_url,
        caller_id,
    )

    client = get_cloud_proxy_client()
    try:
        req = client.build_request(
            envelope.method,
            pinned_url,
            headers=_forward_headers(envelope.headers),
            content=envelope.body if envelope.body is not None else None,
            extensions=extensions,
            timeout=timeout,
        )
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        # No response to close on a request error; the shared client stays open.
        logger.warning('cloud_proxy upstream request failed: %s', exc)
        raise HTTPException(status_code=502, detail='upstream request failed') from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _RESPONSE_HOP_BY_HOP_HEADERS
    }

    async def relay() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get('content-type'),
    )
