"""MCP tool-call route for the OpenHands App Server.

``POST /api/v1/mcp/servers/{server_name}/call-tool`` calls one tool on an MCP
server the caller has connected, with the credentials stored in their settings.
It serves callers that hold no MCP credential of their own and cannot start an
agent for the call: an automation script that polls an issue tracker
authenticates with its run's API key and reads through the connection the user
already made, instead of asking for a second, provider-specific API token.

The call runs the same SDK probe as ``POST /api/v1/mcp/test`` and is validated
the same way (remote transports only, SSRF guard); ``mcp_test_router`` documents
the threat model. Two things differ:

- The server comes from the caller's stored settings, never from the request,
  so the route only reaches servers the user connected and still has enabled.
- OAuth servers are called without a browser. The stored tokens are used and,
  once expired, refreshed; the refreshed state replaces the server's
  ``auth.state``, because a provider that rotates refresh tokens has revoked
  the one the call started from. A connection that needs the user's consent
  again fails the call instead of waiting for it.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastmcp.client.auth.oauth import OAuth

from openhands.agent_server.mcp_router import (
    MCPTestFailure,
    MCPToolCallResult,
    MCPToolCallSpec,
    _probe_mcp_server,
)
from openhands.app_server.mcp.mcp_oauth_router import _oauth_client_kwargs
from openhands.app_server.mcp.mcp_test_router import (
    MCPTestRequestBody,
    _scrub_secrets,
    _secret_values,
    prepare_probe_request,
)
from openhands.app_server.settings.settings_store import SettingsStore
from openhands.app_server.user_auth import get_user_settings_store
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.sdk.mcp.config import (
    MCPOAuthAuthCredential,
    MCPServer,
    enabled_mcp_servers,
)
from openhands.sdk.mcp.oauth import MCPOAuth
from storage.mcp_config import serialize_mcp_config

# Bounds the connection, ``tools/list`` and the tool call, each on its own.
_TOOL_CALL_TIMEOUT_SECONDS = 60.0

router = APIRouter(
    prefix='/mcp',
    tags=['MCP'],
    dependencies=get_dependencies(),
)


class _StoredTokenOAuth(MCPOAuth):
    """OAuth client for a call nobody is watching: stored tokens or a refresh.

    FastMCP answers a provider's request for consent by opening a browser and
    waiting on a loopback callback server, which no caller of this route can
    complete.
    """

    async def redirect_handler(self, authorization_url: str) -> None:
        raise RuntimeError(
            'The MCP server must be authorized again; reconnect it in settings.'
        )


def _oauth_factory(
    _server_name: str,
    _server: MCPServer,
    auth: MCPOAuthAuthCredential,
    oauth_token_storage: Any,
) -> OAuth:
    return _StoredTokenOAuth(
        **_oauth_client_kwargs(auth.authentication, oauth_token_storage)
    )


@router.post(
    '/servers/{server_name}/call-tool',
    response_model=MCPToolCallResult,
    summary='Call a tool on a connected MCP server',
    description=(
        'Call one tool on the MCP server stored under `server_name` in the '
        "caller's settings, using its stored credentials, and return the "
        "tool's text output. A tool that reports a failure still answers 200, "
        'with `is_error` set; 404 means no enabled server has that name, and '
        '502 that the server could not be reached or must be authorized again. '
        'OAuth tokens refreshed by the call are saved to the server.'
    ),
)
async def call_mcp_tool(
    server_name: str,
    tool_call: MCPToolCallSpec,
    settings_store: SettingsStore | None = Depends(get_user_settings_store),
) -> MCPToolCallResult:
    """Call a tool on one of the caller's connected MCP servers."""
    settings = await settings_store.load() if settings_store else None
    server = (
        enabled_mcp_servers(settings.agent_settings.mcp_config).get(server_name)
        if settings
        else None
    )
    if settings_store is None or settings is None or server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'No connected MCP server is named {server_name!r}',
        )

    stored_server = (serialize_mcp_config({server_name: server}) or {})[server_name]
    request = await prepare_probe_request(
        MCPTestRequestBody(
            name=server_name,
            server=stored_server,
            timeout=_TOOL_CALL_TIMEOUT_SECONDS,
            tool_call=tool_call,
        ),
        settings,
    )

    loop = asyncio.get_running_loop()
    response = _scrub_secrets(
        await loop.run_in_executor(
            None, _probe_mcp_server, request, None, _oauth_factory
        ),
        _secret_values(request.resolved_server),
    )
    if isinstance(response, MCPTestFailure):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=response.error
        )

    oauth_auth = server.oauth_auth
    stored_state = (
        oauth_auth.state.to_response() if oauth_auth and oauth_auth.state else None
    )
    if response.oauth_state is not None and response.oauth_state != stored_state:
        # Written only when the call refreshed the tokens: saving the state it
        # started from could overwrite newer tokens a sandbox stored meanwhile.
        # A full map without ``null`` entries replaces the stored catalog
        # wholesale (``Settings.update``), so every server is resent.
        serialized = serialize_mcp_config(settings.agent_settings.mcp_config) or {}
        serialized[server_name]['auth']['state'] = response.oauth_state.model_dump(
            mode='json', exclude_none=True
        )
        settings.update({'agent_settings_diff': {'mcp_config': serialized}})
        await settings_store.store(settings)

    # The probe reports a result whenever it was asked to call a tool.
    assert response.tool_result is not None
    return response.tool_result
