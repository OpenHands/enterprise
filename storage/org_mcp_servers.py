"""Organization-scoped MCP server listings (OHE-3091).

An org admin defines an MCP server listing once and every member's
conversation launches with it. Each listing is managed either by the
organization (``managed_by='org'``: the admin's shared credential rides inline
in the server config, so everyone acts under one identity) or by the user
(``managed_by='user'``, the default: the config references a personal secret
with the existing ``${SECRET_NAME}`` placeholder and each member connects by
creating that secret under their own name).

The listings live on ``org.mcp_servers`` (EncryptedJSON) in the envelope
``{servers: {<name>: {managed_by, server}}}`` where ``server`` is the SDK
``MCPServer`` dump in plaintext -- the column is the at-rest encryption
boundary, exactly like ``org.llm_profiles``. They are never written into any
member's ``org_member.mcp_config``; the launch path merges them under the
member's own servers at conversation start.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.mcp.config import MCPServer, dump_mcp_config
from storage.mcp_config import coerce_persisted_mcp_config

if TYPE_CHECKING:
    from storage.org import Org

OrgMcpManagedBy = Literal['org', 'user']

# Same rule as the settings UI applies to member server names.
ORG_MCP_SERVER_NAME_PATTERN = r'^[a-zA-Z0-9_-]+$'
# The system OpenHands MCP server (Tavily proxy) is injected under this name
# at conversation start and a custom server of the same name replaces it, so
# an org listing may not take it.
RESERVED_MCP_SERVER_NAMES = frozenset({'default'})


class OrgMcpServer(BaseModel):
    """One listing: who supplies the credential, and the server config."""

    model_config = ConfigDict(extra='forbid')

    managed_by: OrgMcpManagedBy = 'user'
    server: MCPServer


def load_org_mcp_servers(org: Org) -> dict[str, OrgMcpServer]:
    """Read the org's listings, skipping (and logging) any that no longer parse.

    Degrades per entry rather than 500-ing a launch: a listing the current SDK
    model rejects is left out and every other listing still applies.
    """
    raw = org.mcp_servers
    servers = raw.get('servers') if isinstance(raw, Mapping) else None
    if not isinstance(servers, Mapping):
        return {}
    listings: dict[str, OrgMcpServer] = {}
    for name, entry in servers.items():
        if not isinstance(name, str) or not isinstance(entry, Mapping):
            continue
        try:
            server = coerce_persisted_mcp_config({name: entry.get('server')})[name]
            listings[name] = OrgMcpServer(
                managed_by=entry.get('managed_by', 'user'), server=server
            )
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                'Skipping org MCP server listing %r of org %s: %s',
                name,
                org.id,
                type(exc).__name__,
            )
    return listings


def dump_org_mcp_servers(listings: Mapping[str, OrgMcpServer]) -> dict[str, Any]:
    """The plaintext envelope for the encrypted column (never an API response)."""
    return {
        'servers': {
            name: {
                'managed_by': entry.managed_by,
                'server': dump_mcp_config(
                    {name: entry.server}, context={'expose_secrets': 'plaintext'}
                )[name],
            }
            for name, entry in listings.items()
        }
    }


def redacted_org_mcp_server(name: str, entry: OrgMcpServer) -> dict[str, Any]:
    """The server config with every credential masked, for API reads.

    ``dump_mcp_config`` exposes plaintext when given no context, so the
    redacting context is passed explicitly.
    """
    return dump_mcp_config({name: entry.server}, context={'expose_secrets': False})[
        name
    ]


def org_managed_server_names(listings: Mapping[str, OrgMcpServer]) -> set[str]:
    """Names of the listings whose credential the organization supplies."""
    return {name for name, entry in listings.items() if entry.managed_by == 'org'}
