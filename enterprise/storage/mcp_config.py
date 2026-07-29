"""Compatibility helpers for MCP configuration stored outside agent settings."""

from typing import Any

from openhands.sdk.mcp.config import MCPServer, dump_mcp_config
from openhands.sdk.settings import validate_agent_settings

_DETACHED_MCP_AGENT_SETTINGS_SCHEMA_VERSION = 4


def coerce_persisted_mcp_config(value: object) -> dict[str, MCPServer]:
    """Load detached MCP settings through the SDK's v4-v5 migration.

    Database migration 137 separated MCP from its parent settings object, so
    the SDK can no longer see the schema version that described this fragment.
    Treat detached data as v4, the last wrapped/scalar-auth representation.
    The SDK migration also accepts already-flat v5 server maps.
    """
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
