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

OAuth (``auth.strategy == 'oauth2'``): an ``org``-managed listing stores the
tokens the admin obtained by authorizing once (``auth.state``), so everyone
acts under that identity. A ``user``-managed listing stores only the OAuth
client configuration; each member authorizes with their own account and the
resulting tokens live on ``org_member.org_mcp_oauth_states`` keyed by listing
name, injected into ``auth.state`` at launch (``org_launch_mcp_servers``) only
while the listing still points at the URL and client they were issued for.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.mcp.config import MCPOAuthState, MCPServer, dump_mcp_config
from storage.mcp_config import coerce_persisted_mcp_config

if TYPE_CHECKING:
    from storage.org import Org
    from storage.org_member import OrgMember

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


def has_oauth_tokens(server: MCPServer) -> bool:
    """Whether an ``oauth2`` server carries an access token (was authorized)."""
    auth = server.oauth_auth
    return (
        auth is not None
        and auth.state is not None
        and auth.state.tokens is not None
        and auth.state.tokens.access_token is not None
    )


def takes_member_oauth_state(entry: OrgMcpServer) -> bool:
    """Whether each member authorizes this listing with their own account."""
    return entry.managed_by == 'user' and entry.server.oauth_auth is not None


def with_oauth_state(server: MCPServer, state: MCPOAuthState | None) -> MCPServer:
    """``server`` with its ``oauth2`` credential's ``state`` replaced (or cleared)."""
    auth = server.oauth_auth
    if auth is None:
        return server
    return server.model_copy(update={'auth': auth.model_copy(update={'state': state})})


class MemberOAuthConnection(BaseModel):
    """A member's tokens for one listing, bound to what they were issued for."""

    model_config = ConfigDict(extra='forbid')

    server_url: str
    client_id: str | None = None
    state: MCPOAuthState

    @classmethod
    def for_server(
        cls, server: MCPServer, state: MCPOAuthState
    ) -> MemberOAuthConnection:
        return cls(
            server_url=(server.url or '').rstrip('/'),
            client_id=oauth_client_id(server),
            state=state,
        )

    def applies_to(self, server: MCPServer) -> bool:
        """False once an admin repointed the listing: the tokens must not follow."""
        return (
            server.url is not None
            and self.server_url == server.url.rstrip('/')
            and self.client_id == oauth_client_id(server)
        )


def oauth_client_id(server: MCPServer) -> str | None:
    auth = server.oauth_auth
    return auth.authentication.client_id if auth and auth.authentication else None


def load_member_oauth_connections(
    member: OrgMember,
) -> dict[str, MemberOAuthConnection]:
    """A member's OAuth connections to ``user``-managed listings, by listing name.

    Entries that no longer parse are skipped so one bad entry never blocks a
    launch.
    """
    raw = member.org_mcp_oauth_states
    if not isinstance(raw, Mapping):
        return {}
    connections: dict[str, MemberOAuthConnection] = {}
    for name, entry in raw.items():
        try:
            connections[name] = MemberOAuthConnection.model_validate(entry)
        except ValidationError as exc:
            logger.warning(
                'Skipping org MCP OAuth connection %r of member %s: %s',
                name,
                member.user_id,
                type(exc).__name__,
            )
    return connections


def dump_member_oauth_connections(
    connections: Mapping[str, MemberOAuthConnection],
) -> dict[str, Any]:
    """The plaintext map for the encrypted ``org_member`` column."""
    return {
        name: connection.model_dump(
            mode='json', exclude_none=True, context={'expose_secrets': 'plaintext'}
        )
        for name, connection in connections.items()
    }


def org_launch_mcp_servers(
    org: Org,
    member: OrgMember | None,
    listings: Mapping[str, OrgMcpServer] | None = None,
) -> dict[str, MCPServer]:
    """The org's listings as ``member``'s conversation launches with them.

    A ``user``-managed OAuth listing carries no tokens of its own: the
    member's own state (obtained by authorizing from Settings > MCP) is
    injected into ``auth.state``. Without one the listing launches without
    tokens and the conversation service leaves it out, the same way it leaves
    out a listing whose ``${SECRET}`` the member has not set.
    """
    if listings is None:
        listings = load_org_mcp_servers(org)
    connections = load_member_oauth_connections(member) if member is not None else {}
    servers: dict[str, MCPServer] = {}
    for name, entry in listings.items():
        server = entry.server
        if takes_member_oauth_state(entry):
            connection = connections.get(name)
            server = with_oauth_state(
                server,
                connection.state
                if connection is not None and connection.applies_to(server)
                else None,
            )
        servers[name] = server
    return servers
