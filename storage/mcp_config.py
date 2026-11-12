"""Compatibility helpers for MCP configuration stored outside agent settings."""

from collections.abc import Mapping
from typing import Any

from openhands.sdk.mcp.config import (
    MCPServer,
    coerce_mcp_config,
    drop_mcp_servers_without_endpoint,
    dump_mcp_config,
)
from openhands.sdk.settings import validate_agent_settings

_DETACHED_MCP_AGENT_SETTINGS_SCHEMA_VERSION = 4


def coerce_persisted_mcp_config(value: object) -> dict[str, MCPServer]:
    """Normalize detached legacy or current MCP configuration.

    For stored data only. A server stored with neither ``command`` nor ``url``
    (accepted before SDK 1.61) is dropped with a warning, as the SDK's own
    settings migration does, so the other servers still load. Validate a
    caller's input with ``coerce_mcp_config``, which rejects such a server.
    """
    if not (
        isinstance(value, Mapping) and isinstance(value.get('mcpServers'), Mapping)
    ):
        if isinstance(value, Mapping):
            value = drop_mcp_servers_without_endpoint(value)
        return coerce_mcp_config(value)

    settings = validate_agent_settings(
        {
            'schema_version': _DETACHED_MCP_AGENT_SETTINGS_SCHEMA_VERSION,
            'mcp_config': {} if value is None else value,
        }
    )
    return settings.mcp_config


def serialize_mcp_config(value: object) -> dict[str, Any] | None:
    """Return the canonical, plaintext representation for encrypted storage."""
    if value is None:
        return None
    return dump_mcp_config(
        coerce_persisted_mcp_config(value),
        context={'expose_secrets': 'plaintext'},
    )
