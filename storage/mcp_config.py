"""Compatibility helpers for MCP configuration stored outside agent settings."""

from collections.abc import Mapping
from typing import Any

from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config, dump_mcp_config
from openhands.sdk.settings import validate_agent_settings

_DETACHED_MCP_AGENT_SETTINGS_SCHEMA_VERSION = 4


def coerce_persisted_mcp_config(value: object) -> dict[str, MCPServer]:
    """Normalize detached legacy or current MCP configuration."""
    if not (
        isinstance(value, Mapping) and isinstance(value.get('mcpServers'), Mapping)
    ):
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
