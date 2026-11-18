"""Single-server MCP mutations for ``/api/v1/settings/mcp/{settings_key}``.

The hosted counterpart of the agent-server's atomic MCP routes (SDK
``POST/PATCH/DELETE /api/settings/mcp/{settings_key}``): each function maps the
stored personal catalog to the new one and touches only the named server. The
settings store runs them under its own lock (``SettingsStore.update_mcp_config``),
so concurrent mutations never overwrite each other's siblings.
"""

from collections.abc import Mapping
from typing import Any

from openhands.app_server.settings.settings_models import (
    _is_redacted_mcp_secret,
    _mcp_endpoint_identity,
    _normalize_mcp_config,
    _restore_stored_mcp_state,
)
from openhands.sdk.mcp.config import MCPServer, dump_mcp_config
from openhands.sdk.settings.model import _merge_patch


class MCPServerAlreadyExistsError(Exception):
    """Raised when creating a server whose settings key is taken."""


class MCPServerNotFoundError(Exception):
    """Raised when patching or deleting a server that is not stored."""


def _drop_redacted(value: Any) -> Any:
    """Remove redacted leaves: in a patch, the mask means "keep the stored value"."""
    if isinstance(value, dict):
        return {
            key: _drop_redacted(item)
            for key, item in value.items()
            if not _is_redacted_mcp_secret(item)
        }
    return value


def create_mcp_server(
    servers: Mapping[str, MCPServer], settings_key: str, server: dict[str, Any]
) -> dict[str, MCPServer]:
    """Add ``server`` under a new key; siblings are kept as stored.

    Redacted credentials are restored the way ``POST /api/v1/settings`` restores
    them, so a rename (create the new key, then delete the old one) keeps the
    credential of the stored server with the same endpoint.
    """
    if settings_key in servers:
        raise MCPServerAlreadyExistsError(settings_key)
    restored = _restore_stored_mcp_state({settings_key: server}, servers)
    return {**servers, **_normalize_mcp_config(restored)}


def patch_mcp_server(
    servers: Mapping[str, MCPServer], settings_key: str, patch: dict[str, Any]
) -> dict[str, MCPServer]:
    """Apply an RFC 7386 merge patch to one stored server.

    Omitted and redacted fields keep their stored value; ``null`` clears a
    field. An ``auth`` of a different ``strategy`` replaces the stored one
    instead of merging into it. Stored secrets are bound to the stored endpoint,
    as in ``POST /api/v1/settings``: when the patch changes the URL or the stdio
    invocation, only the secrets the patch sends in plaintext are kept.
    """
    stored_server = servers.get(settings_key)
    if stored_server is None:
        raise MCPServerNotFoundError(settings_key)
    patch = _drop_redacted(patch)
    stored = _dump_server(settings_key, stored_server, 'plaintext')
    merged = _merge_server_patch(stored, patch)
    if _mcp_endpoint_identity(merged) != _mcp_endpoint_identity(stored):
        masked = _dump_server(settings_key, stored_server, 'redacted')
        merged = _drop_redacted(_merge_server_patch(masked, patch))
    return {**servers, **_normalize_mcp_config({settings_key: merged})}


def _dump_server(settings_key: str, server: MCPServer, secrets: str) -> dict[str, Any]:
    return dump_mcp_config({settings_key: server}, context={'expose_secrets': secrets})[
        settings_key
    ]


def _merge_server_patch(
    stored: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    auth_patch = patch.get('auth')
    stored_auth = stored.get('auth')
    if (
        isinstance(auth_patch, dict)
        and isinstance(stored_auth, dict)
        and auth_patch.get('strategy', stored_auth.get('strategy'))
        != stored_auth.get('strategy')
    ):
        stored = {key: value for key, value in stored.items() if key != 'auth'}
    return _merge_patch(stored, patch)


def delete_mcp_server(
    servers: Mapping[str, MCPServer], settings_key: str
) -> dict[str, MCPServer]:
    if settings_key not in servers:
        raise MCPServerNotFoundError(settings_key)
    return {name: server for name, server in servers.items() if name != settings_key}
