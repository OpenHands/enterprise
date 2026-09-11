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
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from openhands.agent_server.mcp_router import (
    MCPTestFailure,
    MCPTestRequest,
    MCPTestResponse,
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

router = APIRouter(
    prefix='/mcp',
    tags=['MCP'],
    dependencies=get_dependencies(),
)


def _collect_secret_values(plain: Any, redacted: Any, secrets: set[str]) -> None:
    """Collect the plaintext leaves the SDK model marks as secrets.

    Walks a plaintext ``MCPServer`` dump alongside its redacted dump and keeps
    every string that the redacted dump masks, so the set follows the SDK's own
    definition of what is secret (headers, env, auth credentials, OAuth tokens).
    """
    if isinstance(plain, dict) and isinstance(redacted, dict):
        for key, value in plain.items():
            _collect_secret_values(value, redacted.get(key), secrets)
    elif isinstance(plain, list) and isinstance(redacted, list):
        for item, redacted_item in zip(plain, redacted, strict=False):
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


def _scrub_secrets(response: MCPTestResponse, secrets: set[str]) -> MCPTestResponse:
    """Mask secret values that upstream error / tool text may echo back."""
    if not secrets:
        return response

    def scrub(text: str) -> str:
        for secret in sorted(secrets, key=len, reverse=True):
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
        'and timeout failures; `stdio` servers are rejected with 422 because '
        'they only run inside the sandbox.'
    ),
)
async def test_mcp_server(
    payload: dict[str, Any],
    settings: Settings | None = Depends(get_user_settings),
) -> MCPTestResponse:
    """Probe a single remote MCP server config and report whether it works."""
    name = str(payload.get('name') or _DEFAULT_SERVER_NAME)
    server = payload.get('server')
    if isinstance(server, dict):
        # Always run the restore pass: with no stored match the redaction
        # marker is dropped rather than sent upstream as a literal credential.
        restored = _preserve_redacted_mcp_secrets(
            {name: server},
            settings.agent_settings.mcp_config if settings else None,
        )
        payload = {**payload, 'server': restored[name]}

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
    return _scrub_secrets(response, _secret_values(resolved_server))
