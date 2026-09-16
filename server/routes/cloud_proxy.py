"""Authenticated proxy for browser requests to OpenHands runtime hosts.

Cloud Canvas cannot call a per-conversation runtime directly from the browser:
the runtime host is not the same origin as the Canvas and may not expose CORS
headers. The Canvas client therefore sends a request envelope to this backend,
which forwards it to the runtime after validating the destination.

This endpoint is intentionally limited to OpenHands-managed runtime hosts. A
generic authenticated URL fetcher would be an SSRF primitive because session
API keys are accepted by this server.

By default only public ``https`` runtime hosts under the configured runtime
host suffixes (``*.prod-runtime.all-hands.dev`` / ``*.staging-runtime.all-hands.dev``)
are accepted. Self-hosted or local deployments that run runtimes on loopback
can opt in with ``CLOUD_PROXY_ALLOW_LOCAL_RUNTIME=1``; this is dev-only and
must never be enabled in production.

SSRF hardening: the destination host is validated, resolved once, and the
resolved IP is pinned for the actual connection (with the original hostname
preserved for TLS SNI / cert validation). The *final* request URL is
re-validated after the host and path are combined, so a crafted ``path``
cannot redirect the connection past the host allowlist. Redirects are not
followed and transport-proxy env vars are ignored.
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
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, JsonValue, field_validator

from openhands.app_server.user_auth import get_user_id
from server.logger import logger

_DEFAULT_RUNTIME_HOST_SUFFIXES = (
    '.prod-runtime.all-hands.dev',
    '.staging-runtime.all-hands.dev',
)

# Upper bound on the caller-supplied timeout so a single request cannot hold a
# backend->runtime connection open indefinitely.
_MAX_TIMEOUT_SECONDS = 60.0
_DEFAULT_TIMEOUT_SECONDS = 30.0


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
    """Request envelope for forwarding a browser call to a runtime host."""

    host: str = Field(description='Absolute upstream OpenHands runtime host')
    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
    path: str = Field(
        description='Upstream absolute path (must start with /), including query string'
    )
    headers: dict[str, str] = Field(default_factory=dict)
    body: JsonValue | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=_MAX_TIMEOUT_SECONDS)

    @field_validator('path')
    @classmethod
    def validate_path(cls, value: str) -> str:
        # A non-"/"-prefixed path turns the validated host into RFC userinfo
        # when concatenated (e.g. path="@169.254.169.254/...") and redirects
        # the actual connection past the host allowlist. Require an absolute
        # path so host+path cannot re-parse to a different host.
        if not value.startswith('/'):
            raise ValueError('path must be an absolute path starting with /')
        return value

    @field_validator('host')
    @classmethod
    def validate_host(cls, value: str) -> str:
        parsed = urlparse(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError('host must not include an invalid port') from exc

        allow_local = _allow_local_runtime()
        allowed_schemes = ('https', 'http') if allow_local else ('https',)
        allows_port = allow_local  # only loopback runtimes carry a port

        if (
            parsed.scheme not in allowed_schemes
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ('', '/')
            or (port and not allows_port)
        ):
            scheme_desc = 'https' if not allow_local else 'https (or http locally)'
            port_desc = '' if not allows_port else ', port allowed locally'
            raise ValueError(
                f'host must be an absolute {scheme_desc} URL without userinfo, '
                f'query, fragment, or path{port_desc}'
            )

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
            host_allowed = (
                ip is not None and ip.is_loopback
            ) or hostname == 'localhost'

        if not host_allowed:
            raise ValueError('host is not an allowed OpenHands runtime host')

        # Preserve the port for loopback runtimes; cloud runtime hosts have none.
        if port and allow_local:
            return (
                f'{"http" if parsed.scheme == "http" else "https"}://{hostname}:{port}'
            )
        return f'{parsed.scheme}://{hostname}'


cloud_proxy_router = APIRouter(prefix='/api/cloud-proxy', tags=['Cloud Proxy'])


def _resolve_target(host: str) -> tuple[str, str, int]:
    """Resolve the host and return (hostname, pinned_ip, port).

    Rejects non-public addresses unless local mode is on. The returned IP is
    pinned for the actual connection so a DNS record cannot rebind to a
    private address between the check and the connect.
    """
    parsed = urlparse(host)
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail='host is required')

    allow_local = _allow_local_runtime()
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)

    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
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
    return parsed.hostname, addresses[0][4][0], port


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
    user_id: str | None = Depends(get_user_id),
) -> StreamingResponse:
    """Forward an authenticated request to an allowed OpenHands runtime."""
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    hostname, ip, port = await asyncio.to_thread(_resolve_target, envelope.host)

    # Re-validate the *final* URL: combine host + path and assert the hostname
    # is unchanged, so a crafted path cannot redirect the connection.
    final_url = f'{envelope.host}{envelope.path}'
    final_host = urlparse(final_url).hostname
    if not final_host or final_host.lower().rstrip('.') != hostname:
        raise HTTPException(status_code=400, detail='invalid request path')

    # Pin the resolved IP in the URL while preserving the original hostname for
    # TLS SNI / certificate validation via the sni_hostname extension. This
    # closes the DNS-rebinding window between resolve and connect.
    scheme = urlparse(envelope.host).scheme
    port_suffix = f':{port}' if port not in (443, 80) else ''
    ip_host = f'[{ip}]' if ':' in ip else ip  # bracket IPv6 literals
    pinned_url = f'{scheme}://{ip_host}{port_suffix}{envelope.path}'
    extensions = {'sni_hostname': hostname} if scheme == 'https' else {}

    timeout = envelope.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS
    logger.debug(
        'cloud_proxy forwarding %s %s (user=%s)', envelope.method, final_url, user_id
    )

    try:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        req = client.build_request(
            envelope.method,
            pinned_url,
            headers=_forward_headers(envelope.headers),
            json=envelope.body if envelope.body is not None else None,
            extensions=extensions,
        )
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
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
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get('content-type'),
    )
