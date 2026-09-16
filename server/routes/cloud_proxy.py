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
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, JsonValue, field_validator

from server.logger import logger

_DEFAULT_RUNTIME_HOST_SUFFIXES = (
    ".prod-runtime.all-hands.dev",
    ".staging-runtime.all-hands.dev",
)


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

_RESPONSE_HOP_BY_HOP_HEADERS = frozenset(
    {
        'connection',
        'content-encoding',
        'content-length',
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
        description='Upstream absolute path, including query string'
    )
    headers: dict[str, str] = Field(default_factory=dict)
    body: JsonValue | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)

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

        if (
            parsed.scheme not in allowed_schemes
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ('', '/')
        ):
            raise ValueError(
                'host must be an absolute https URL without a port'
                + ('' if not allow_local else ' (http allowed locally)')
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
            host_allowed = (ip is not None and ip.is_loopback) or hostname == 'localhost'

        if not host_allowed:
            raise ValueError('host is not an allowed OpenHands runtime host')

        # Preserve the port for loopback runtimes; cloud runtime hosts have none.
        if port and allow_local:
            return f'{"http" if parsed.scheme == "http" else "https"}://{hostname}:{port}'
        return f'{parsed.scheme}://{hostname}'


cloud_proxy_router = APIRouter(prefix='/api/cloud-proxy', tags=['Cloud Proxy'])


def _ensure_reachable_target(host: str) -> None:
    """Resolve the host and reject non-public addresses unless local mode is on."""
    parsed = urlparse(host)
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail='host is required')

    allow_local = _allow_local_runtime()
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)

    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )
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


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep end-to-end headers while letting httpx own transport headers."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _REQUEST_HOP_BY_HOP_HEADERS
    }


@cloud_proxy_router.post(
    '',
    response_class=Response,
    responses={200: {'description': 'Upstream response'}},
)
async def proxy_cloud_request(request: CloudProxyRequest) -> Response:
    """Forward an authenticated request to an allowed OpenHands runtime."""
    await asyncio.to_thread(_ensure_reachable_target, request.host)
    url = f'{request.host}{request.path}'
    timeout = request.timeout_seconds or 30.0

    logger.debug(
        'cloud_proxy forwarding %s %s', request.method, url
    )

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            upstream = await client.request(
                request.method,
                url,
                headers=_forward_headers(request.headers),
                json=request.body if request.body is not None else None,
            )
    except httpx.RequestError as exc:
        logger.warning('cloud_proxy upstream request failed: %s', exc)
        raise HTTPException(
            status_code=502, detail='upstream request failed'
        ) from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _RESPONSE_HOP_BY_HOP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get('content-type'),
    )
