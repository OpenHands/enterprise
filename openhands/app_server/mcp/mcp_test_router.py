"""MCP connection-test route for the OpenHands App Server.

``POST /api/v1/mcp/test`` lets the settings UIs (bundled frontend and Agent
Canvas) verify a remote MCP server before or after saving it. It reuses the
agent-server's single-server probe so the response contract is identical to
the sandbox's ``POST /api/mcp/test``: HTTP 200 with ``ok=true`` and the tool
names, or ``ok=false`` with a coarse ``error_kind`` (``timeout`` /
``connection`` / ``unknown``).

Differences from the sandbox endpoint, by design:

- Only remote transports (``sse`` / ``http``) are probed. ``stdio`` servers run
  inside the sandbox and must never spawn a process on the app server.
- Redacted secrets (``**********``) submitted for an already-stored server are
  restored from the user's persisted settings — the same rules ``POST
  /api/v1/settings`` applies — so a test exercises exactly the credentials a
  save would persist, without the browser ever seeing them. Restored values are
  scrubbed from the response text.
- OAuth-authenticated servers are not probed: the browser-coordinated OAuth
  flow only exists on a local agent-server.
- The probe originates from the app-server pod, not from a sandbox, so network
  reachability can differ between the two.

Threat model
------------
The probe makes the app-server pod open an outbound HTTP/SSE connection to a
user-supplied URL, which is a server-side request forgery (SSRF) surface. The
route therefore:

- accepts only ``http`` / ``https`` URLs;
- resolves the target host before probing and rejects addresses that are never
  a legitimate MCP host but are classic pivots from a pod: loopback (the pod's
  own sidecars), link-local (cloud instance-metadata services such as
  ``169.254.169.254``), unspecified, multicast and reserved ranges, including
  their IPv4-mapped IPv6 forms. RFC 1918 / ULA private ranges stay reachable on
  purpose: self-hosted deployments point at MCP servers on their internal
  network, the same hosts the sandbox connects to at conversation start;
- performs nothing beyond the MCP handshake and ``tools/list`` (plus an optional
  caller-chosen tool call) and never echoes response bodies: only the SDK's
  condensed error string, scrubbed of secrets, reaches the client;
- inherits the app-wide per-client request rate limit (``RateLimitMiddleware``).

Residual risk: the address check and the probe resolve DNS independently, so a
host whose answer changes between the two (DNS rebinding) can bypass the check.
Set ``MCP_TEST_ALLOW_LOCAL_TARGETS=true`` to disable the address check for local
development against MCP servers on localhost.
"""

import asyncio
import ipaddress
import json
import os
import socket
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from openhands.agent_server.mcp_router import (
    MCPTestFailure,
    MCPTestRequest,
    MCPTestResponse,
    MCPToolCallSpec,
    _probe_mcp_server,
)
from openhands.app_server.settings.settings_models import (
    Settings,
    _preserve_redacted_mcp_secrets,
)
from openhands.app_server.user_auth import get_user_settings
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE

_DEFAULT_SERVER_NAME = 'test-server'
_ALLOWED_URL_SCHEMES = frozenset({'http', 'https'})
# Local development only: lets the probe reach MCP servers on localhost.
_ALLOW_LOCAL_TARGETS = os.environ.get('MCP_TEST_ALLOW_LOCAL_TARGETS', '').lower() in (
    '1',
    'true',
    'yes',
)

router = APIRouter(
    prefix='/mcp',
    tags=['MCP'],
    dependencies=get_dependencies(),
)


class MCPTestRequestBody(BaseModel):
    """Body for ``POST /api/v1/mcp/test``.

    ``server`` is deliberately an untyped map: it goes through the same
    redacted-secret restoration as ``POST /api/v1/settings`` *before* the SDK's
    ``MCPTestRequest`` validates it, because that validation turns the
    ``**********`` marker into an absent secret. The remaining fields mirror
    ``MCPTestRequest`` so the OpenAPI schema documents them and FastAPI rejects
    malformed top-level input before the handler runs.
    """

    name: str = Field(
        default=_DEFAULT_SERVER_NAME,
        min_length=1,
        max_length=128,
        description=(
            'Settings key of the server being tested. Used to restore unchanged '
            '(redacted) secrets from the stored server of the same name.'
        ),
    )
    server: dict[str, Any] = Field(
        description=(
            'One MCP server config in the SDK `mcp_config` entry shape '
            '(`url`, `transport` or `type`, `headers`, `auth`, `timeout`, ...).'
        )
    )
    timeout: float | None = Field(
        default=None,
        gt=0,
        le=120,
        description='Seconds to wait for connection + tools/list to complete.',
    )
    tool_call: MCPToolCallSpec | None = Field(
        default=None,
        description=(
            'Optional read-only tool to invoke after listing succeeds; its '
            'outcome is reported in `tool_result` without affecting `ok`.'
        ),
    )


def _resolve_probe_addresses(
    host: str, port: int | None
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``host`` to the addresses the probe would connect to."""
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
            info[4][0]
        )
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        addresses.append(address)
    return addresses


def _check_probe_target(url: str) -> str | None:
    """Return why ``url`` must not be probed from the app server, or ``None``.

    Blocking (DNS); run it in a worker thread. See the module docstring for the
    threat model behind the rejected address classes.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return 'MCP server URL must be an http:// or https:// URL with a host.'
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES or not host:
        return 'MCP server URL must be an http:// or https:// URL with a host.'
    if _ALLOW_LOCAL_TARGETS:
        return None
    try:
        addresses = _resolve_probe_addresses(host, port)
    except (OSError, UnicodeError, ValueError):
        # An unresolvable host is reported by the probe itself as a
        # connection failure; nothing to protect against here.
        return None
    for address in addresses:
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ):
            return (
                f'MCP server host {host!r} resolves to {address}, which cannot '
                'be probed from the app server.'
            )
    return None


def _collect_secret_values(plain: Any, redacted: Any, secrets: set[str]) -> None:
    """Collect the plaintext leaves the SDK model marks as secrets.

    Walks a plaintext ``MCPServer`` dump alongside its redacted dump and keeps
    every string that the redacted dump masks, so the set follows the SDK's own
    definition of what is secret (headers, env, auth credentials, OAuth tokens).
    Both dumps come from the same model instance, so their shapes match.
    """
    if isinstance(plain, dict) and isinstance(redacted, dict):
        for key, value in plain.items():
            _collect_secret_values(value, redacted.get(key), secrets)
    elif isinstance(plain, list) and isinstance(redacted, list):
        for item, redacted_item in zip(plain, redacted, strict=True):
            _collect_secret_values(item, redacted_item, secrets)
    elif (
        isinstance(plain, str)
        and plain
        and plain != REDACTED_SECRET_VALUE
        and redacted == REDACTED_SECRET_VALUE
    ):
        secrets.add(plain)


def _secret_values(server: MCPServer) -> set[str]:
    secrets: set[str] = set()
    _collect_secret_values(
        server.model_dump(mode='json', context={'expose_secrets': 'plaintext'}),
        server.model_dump(mode='json'),
        secrets,
    )
    return secrets


def _secret_variants(secret: str) -> set[str]:
    """The raw secret plus the encoded forms an upstream error may echo it in."""
    return {secret, quote(secret, safe=''), json.dumps(secret)[1:-1]}


def _scrub_secrets(response: MCPTestResponse, secrets: set[str]) -> MCPTestResponse:
    """Mask secret values that upstream error / tool text may echo back."""
    if not secrets:
        return response

    variants = {variant for secret in secrets for variant in _secret_variants(secret)}

    def scrub(text: str) -> str:
        for secret in sorted(variants, key=len, reverse=True):
            text = text.replace(secret, REDACTED_SECRET_VALUE)
        return text

    if isinstance(response, MCPTestFailure):
        return response.model_copy(update={'error': scrub(response.error)})
    if response.tool_result is not None:
        tool_result = response.tool_result.model_copy(
            update={'text': scrub(response.tool_result.text)}
        )
        return response.model_copy(update={'tool_result': tool_result})
    return response


@router.post(
    '/test',
    response_model=MCPTestResponse,
    response_model_exclude_none=True,
    summary='Test an MCP server configuration',
    description=(
        'Connect to a candidate remote MCP server and list its tools without '
        'persisting any settings. Redacted secrets submitted for an already '
        'stored server are restored from the saved configuration before the '
        'connection is attempted. Returns 200 with `ok=false` for connection '
        'and timeout failures; `stdio` servers and URLs that resolve to '
        'loopback or link-local addresses are rejected with 422.'
    ),
)
async def test_mcp_server(
    body: MCPTestRequestBody,
    settings: Settings | None = Depends(get_user_settings),
) -> MCPTestResponse:
    """Probe a single remote MCP server config and report whether it works."""
    # Always run the restore pass: with no stored match the redaction marker
    # is dropped rather than sent upstream as a literal credential.
    restored = _preserve_redacted_mcp_secrets(
        {body.name: body.server},
        settings.agent_settings.mcp_config if settings else None,
    )
    payload = {**body.model_dump(exclude_none=True), 'server': restored[body.name]}

    try:
        request = MCPTestRequest.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Invalid MCP test request: {e}',
        ) from e

    if request.server.type == 'stdio':
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                'stdio MCP servers run inside the sandbox and cannot be tested '
                'from the settings page.'
            ),
        )

    resolved_server = request.resolved_server
    loop = asyncio.get_running_loop()
    rejection = await loop.run_in_executor(
        None, _check_probe_target, resolved_server.url or ''
    )
    if rejection is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=rejection
        )

    if resolved_server.oauth_auth is not None:
        return MCPTestFailure(
            error=(
                'OAuth-authenticated MCP servers cannot be tested from the '
                'settings page.'
            ),
            error_kind='unknown',
        )

    response = await loop.run_in_executor(None, _probe_mcp_server, request, None)
    return _scrub_secrets(response, _secret_values(resolved_server))
