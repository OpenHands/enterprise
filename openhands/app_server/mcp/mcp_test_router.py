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
- ``${NAME}`` / ``${NAME:-default}`` references are expanded from the secrets a
  conversation receives (custom secrets and ``<PROVIDER>_TOKEN`` git tokens),
  never from the app server's environment. A reference without a default that
  matches no secret fails the test by name instead of being sent literally.
- OAuth-authenticated servers are not probed here; ``mcp_oauth_router`` runs
  the browser-coordinated OAuth install flow for them.
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
import re
import socket
from typing import Any, NamedTuple, cast
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
from openhands.app_server.config import depends_user_context
from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.app_server.settings.settings_models import (
    Settings,
    _preserve_redacted_mcp_secrets,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user_auth import get_user_settings
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.skills.utils import expand_variable_references
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE

_DEFAULT_SERVER_NAME = 'test-server'
# The references a conversation expands in its MCP config (SDK skills.utils).
_SECRET_REFERENCE = re.compile(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)(:-[^}]*)?\}')
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
user_context_dependency = depends_user_context()


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


class _SecretExpansion(NamedTuple):
    server: dict[str, Any]
    values: set[str]
    missing: list[str]


def _collect_references(data: Any, names: set[str], required: set[str]) -> None:
    """Collect referenced names; ``required`` holds those used without a default."""
    if isinstance(data, str):
        for match in _SECRET_REFERENCE.finditer(data):
            names.add(match.group(1))
            if match.group(2) is None:
                required.add(match.group(1))
    elif isinstance(data, dict):
        for key, value in data.items():
            _collect_references(key, names, required)
            _collect_references(value, names, required)
    elif isinstance(data, list):
        for item in data:
            _collect_references(item, names, required)


async def _conversation_secret_values(
    names: set[str], user_context: UserContext
) -> dict[str, str]:
    """Values for ``names`` among the secrets a conversation receives.

    Mirrors conversation setup: custom secrets (personal and org-shared), with
    git provider tokens exposed as ``<PROVIDER>_TOKEN`` taking precedence.
    """
    values: dict[str, str] = {}
    custom_secrets = await user_context.get_secrets()
    for name in names:
        source = custom_secrets.get(name)
        value = source.get_value() if source else None
        if value is not None:
            values[name] = value

    if any(name.endswith('_TOKEN') for name in names):
        provider_tokens = cast(
            PROVIDER_TOKEN_TYPE | None, await user_context.get_provider_tokens()
        )
        for provider_type, provider_token in (provider_tokens or {}).items():
            name = f'{provider_type.name}_TOKEN'
            if name in names and provider_token.token:
                token = await user_context.get_latest_token(provider_type)
                if token:
                    values[name] = token
    return values


async def _expand_secret_references(
    server: dict[str, Any], user_context: UserContext
) -> _SecretExpansion:
    names: set[str] = set()
    required: set[str] = set()
    _collect_references(server, names, required)
    if not names:
        return _SecretExpansion(server, set(), [])

    values = await _conversation_secret_values(names, user_context)
    # Only the lookup above: never the app server's own environment.
    expanded = expand_variable_references(
        server, get_secret=values.get, check_env=False
    )
    return _SecretExpansion(
        expanded,
        {value for value in values.values() if value},
        sorted(required - values.keys()),
    )


def _restore_redacted_server(
    body: MCPTestRequestBody, settings: Settings | None
) -> dict[str, Any]:
    # Always run the restore pass: with no stored match the redaction marker
    # is dropped rather than sent upstream as a literal credential.
    restored = _preserve_redacted_mcp_secrets(
        {body.name: body.server},
        settings.agent_settings.mcp_config if settings else None,
    )
    return restored[body.name]


async def prepare_probe_request(
    body: MCPTestRequestBody, settings: Settings | None
) -> MCPTestRequest:
    """Turn a settings-page request into a validated, probe-safe request.

    Shared by the connection test and the OAuth install routes: restores
    redacted secrets from the stored server of the same name, validates the
    result against the SDK model, and rejects ``stdio`` servers and targets
    the SSRF guard refuses with 422.
    """
    return await _validate_probe_request(body, _restore_redacted_server(body, settings))


async def _validate_probe_request(
    body: MCPTestRequestBody, server: dict[str, Any]
) -> MCPTestRequest:
    payload = {**body.model_dump(exclude_none=True), 'server': server}

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

    loop = asyncio.get_running_loop()
    rejection = await loop.run_in_executor(
        None, _check_probe_target, request.resolved_server.url or ''
    )
    if rejection is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=rejection
        )
    return request


@router.post(
    '/test',
    response_model=MCPTestResponse,
    response_model_exclude_none=True,
    summary='Test an MCP server configuration',
    description=(
        'Connect to a candidate remote MCP server and list its tools without '
        'persisting any settings. Redacted secrets submitted for an already '
        'stored server are restored from the saved configuration, and '
        '`${NAME}` secret references are expanded as a conversation would, '
        'before the connection is attempted. Returns 200 with `ok=false` for '
        'connection and timeout failures and for references to secrets that '
        'are not set; `stdio` servers and URLs that resolve to loopback or '
        'link-local addresses are rejected with 422.'
    ),
)
async def test_mcp_server(
    body: MCPTestRequestBody,
    settings: Settings | None = Depends(get_user_settings),
    user_context: UserContext = user_context_dependency,
) -> MCPTestResponse:
    """Probe a single remote MCP server config and report whether it works."""
    expansion = await _expand_secret_references(
        _restore_redacted_server(body, settings), user_context
    )
    if expansion.missing:
        references = ', '.join(f'${{{name}}}' for name in expansion.missing)
        return MCPTestFailure(
            error=(
                f'No secret is set for {references}. Add it on the Secrets '
                'page or correct the reference.'
            ),
            error_kind='unknown',
        )
    request = await _validate_probe_request(body, expansion.server)
    resolved_server = request.resolved_server

    if resolved_server.oauth_auth is not None:
        return MCPTestFailure(
            error=(
                'OAuth-authenticated MCP servers cannot be tested from the '
                'settings page.'
            ),
            error_kind='unknown',
        )

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, _probe_mcp_server, request, None)
    return _scrub_secrets(response, _secret_values(resolved_server) | expansion.values)
