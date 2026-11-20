"""Store for organization-scoped MCP server listings (OHE-3091).

Listings are usable by every member of the org in conversations but can only
be created, edited and deleted by Admins and Owners (``MANAGE_ORG_MCP``).
Mutations serialize on a ``SELECT ... FOR UPDATE`` of the org row, the same
lock the org Agent Profile and LLM profile routers take, and write the whole
collection back onto ``org.mcp_servers``.

A member's own OAuth tokens for ``user``-managed OAuth listings live on their
``org_member`` row (``org_mcp_oauth_states``) and are written under that row's
lock; any member may store or clear their own.
"""

from __future__ import annotations

import builtins
import contextlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from openhands.sdk.mcp.config import MCPOAuthState, MCPServer
from storage.agent_profile_resolution import load_agent_profiles
from storage.database import a_session_maker
from storage.org import Org
from storage.org_mcp_servers import (
    MemberOAuthConnection,
    OrgMcpServer,
    dump_member_oauth_connections,
    dump_org_mcp_servers,
    load_member_oauth_connections,
    load_org_mcp_servers,
    takes_member_oauth_state,
    with_oauth_state,
)
from storage.org_member import OrgMember


class OrgMcpServersOrgMissingError(Exception):
    """Raised when the organization row does not exist."""


class OrgMcpServerNotFoundError(Exception):
    """Raised when no listing of that name exists."""


class OrgMcpServerAlreadyExistsError(Exception):
    """Raised when creating a listing whose name is taken."""


class OrgMcpMemberMissingError(Exception):
    """Raised when the user is not a member of the organization."""


class OrgMcpServerConnectionError(Exception):
    """Raised when a member's OAuth state does not apply to the listing.

    Only a ``user``-managed listing with an ``oauth2`` credential takes a
    per-member state, and the state must carry an access token.
    """


class OrgMcpServerInUseError(Exception):
    """Raised when an org Agent Profile still references the listing.

    Deleting the listing, or handing its credential over to each user, would
    make every launch through that profile fail with an unresolved reference.
    """

    def __init__(self, name: str, profile_names: list[str]) -> None:
        self.name = name
        self.profile_names = profile_names
        super().__init__(
            f'Organization MCP server {name!r} is referenced by organization '
            'agent profile(s): ' + ', '.join(profile_names)
        )


def _same_url(server: MCPServer, url: str) -> bool:
    return server.url is not None and server.url.rstrip('/') == url


def referring_org_profiles(org: Org, name: str) -> list[str]:
    """Names of the org's Agent Profiles whose ``mcp_server_refs`` list ``name``."""
    return sorted(
        summary['name']
        for summary in load_agent_profiles(org).list_summaries()
        if name in (summary['mcp_server_refs'] or [])
    )


@dataclass
class OrgMcpServersStore:
    """CRUD over the org's MCP server listings."""

    org_id: UUID

    async def _org(self) -> Org:
        async with a_session_maker() as session:
            result = await session.execute(select(Org).filter(Org.id == self.org_id))
            org = result.scalars().first()
        if org is None:
            raise OrgMcpServersOrgMissingError(f'Organization {self.org_id} not found')
        return org

    async def list(self) -> dict[str, OrgMcpServer]:
        """Every listing with its credentials in plaintext (runtime use only)."""
        return load_org_mcp_servers(await self._org())

    async def get(self, name: str) -> OrgMcpServer | None:
        return (await self.list()).get(name)

    @contextlib.asynccontextmanager
    async def _locked(self) -> AsyncIterator[tuple[Org, dict[str, OrgMcpServer]]]:
        """Yield ``(org, listings)`` under the org row lock; write back on exit.

        The caller mutates ``listings`` in place. A listing the current model
        can no longer parse is dropped from the write-back (``load_org_mcp_servers``
        skips it), so the collection always round-trips through the SDK model.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(Org).filter(Org.id == self.org_id).with_for_update()
            )
            org = result.scalars().first()
            if org is None:
                raise OrgMcpServersOrgMissingError(
                    f'Organization {self.org_id} not found'
                )
            listings = load_org_mcp_servers(org)
            yield org, listings
            org.mcp_servers = dump_org_mcp_servers(listings)
            await session.commit()

    async def create(self, name: str, entry: OrgMcpServer) -> None:
        async with self._locked() as (_org, listings):
            if name in listings:
                raise OrgMcpServerAlreadyExistsError(
                    f'Organization MCP server {name!r} already exists'
                )
            listings[name] = entry

    async def update(self, name: str, entry: OrgMcpServer) -> None:
        """Replace the listing. Names are immutable: profiles reference them."""
        async with self._locked() as (org, listings):
            current = listings.get(name)
            if current is None:
                raise OrgMcpServerNotFoundError(
                    f'Organization MCP server {name!r} not found'
                )
            if current.managed_by == 'org' and entry.managed_by == 'user':
                # An org profile launches with org-shared secrets only, so a
                # user-managed listing can never resolve for it.
                referrers = referring_org_profiles(org, name)
                if referrers:
                    raise OrgMcpServerInUseError(name, referrers)
            listings[name] = entry

    async def delete(self, name: str) -> None:
        async with self._locked() as (org, listings):
            if name not in listings:
                raise OrgMcpServerNotFoundError(
                    f'Organization MCP server {name!r} not found'
                )
            referrers = referring_org_profiles(org, name)
            if referrers:
                raise OrgMcpServerInUseError(name, referrers)
            del listings[name]
        await self._forget_member_connections(name)

    # ------------------------------------------------------------------
    # Per-member OAuth connections to ``user``-managed OAuth listings
    # ------------------------------------------------------------------

    def _member_query(self, user_id: UUID):
        return select(OrgMember).filter(
            OrgMember.org_id == self.org_id, OrgMember.user_id == user_id
        )

    async def _forget_member_connections(self, name: str) -> None:
        """Drop every member's tokens for a deleted listing."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(OrgMember)
                .filter(
                    OrgMember.org_id == self.org_id,
                    OrgMember.org_mcp_oauth_states.is_not(None),
                )
                .with_for_update()
            )
            for member in result.scalars():
                connections = load_member_oauth_connections(member)
                if connections.pop(name, None) is not None:
                    member.org_mcp_oauth_states = dump_member_oauth_connections(
                        connections
                    )
            await session.commit()

    async def member_connected_names(
        self, user_id: UUID, listings: Mapping[str, OrgMcpServer]
    ) -> set[str]:
        """The ``user``-managed OAuth ``listings`` ``user_id`` holds tokens for."""
        async with a_session_maker() as session:
            result = await session.execute(self._member_query(user_id))
            member = result.scalars().first()
        if member is None:
            return set()
        return {
            name
            for name, connection in load_member_oauth_connections(member).items()
            if name in listings
            and takes_member_oauth_state(listings[name])
            and connection.applies_to(listings[name].server)
        }

    @contextlib.asynccontextmanager
    async def _locked_connections(
        self, user_id: UUID
    ) -> AsyncIterator[dict[str, MemberOAuthConnection]]:
        """Yield the member's connections under the member row lock; write back on exit."""
        async with a_session_maker() as session:
            result = await session.execute(
                self._member_query(user_id).with_for_update()
            )
            member = result.scalars().first()
            if member is None:
                raise OrgMcpMemberMissingError(
                    f'User {user_id} is not a member of organization {self.org_id}'
                )
            connections = load_member_oauth_connections(member)
            yield connections
            member.org_mcp_oauth_states = dump_member_oauth_connections(connections)
            await session.commit()

    async def set_member_oauth_state(
        self, user_id: UUID, name: str, state: MCPOAuthState
    ) -> None:
        """Store the tokens ``user_id`` obtained for the OAuth listing ``name``."""
        if state.tokens is None or state.tokens.access_token is None:
            raise OrgMcpServerConnectionError('OAuth state carries no access token')
        entry = await self.get(name)
        if entry is None:
            raise OrgMcpServerNotFoundError(
                f'Organization MCP server {name!r} not found'
            )
        if not takes_member_oauth_state(entry):
            raise OrgMcpServerConnectionError(
                f'Organization MCP server {name!r} is not a user-managed OAuth server'
            )
        async with self._locked_connections(user_id) as connections:
            connections[name] = MemberOAuthConnection.for_server(entry.server, state)

    async def clear_member_oauth_state(self, user_id: UUID, name: str) -> None:
        """Forget ``user_id``'s tokens for ``name``; a no-op when there are none."""
        async with self._locked_connections(user_id) as connections:
            connections.pop(name, None)

    async def write_back_oauth_state(
        self, user_id: UUID, server_url: str, state: MCPOAuthState
    ) -> builtins.list[str]:
        """Persist OAuth state a member's sandbox refreshed for ``server_url``.

        An ``org``-managed listing at that URL takes the state itself (every
        member shares those tokens); a ``user``-managed one takes it on the
        member's row, and only when the member already holds a connection for
        it (a sandbox never launches with an unauthorized listing). Returns
        the names written; empty when nothing at that URL uses OAuth or the
        sandbox's owner has left the organization.
        """
        # ``builtins``: the ``list`` method above shadows the type in this scope.
        async with a_session_maker() as session:
            result = await session.execute(self._member_query(user_id))
            if result.scalars().first() is None:
                return []
        url = server_url.rstrip('/')
        matches = {
            name: entry
            for name, entry in (await self.list()).items()
            if entry.server.oauth_auth is not None and _same_url(entry.server, url)
        }
        org_managed = [n for n, e in matches.items() if e.managed_by == 'org']
        written: builtins.list[str] = []
        if org_managed:
            async with self._locked() as (_org, listings):
                for name in org_managed:
                    entry = listings.get(name)
                    if entry is not None:
                        listings[name] = OrgMcpServer(
                            managed_by=entry.managed_by,
                            server=with_oauth_state(entry.server, state),
                        )
                        written.append(name)
        user_managed = {n: e for n, e in matches.items() if e.managed_by == 'user'}
        if user_managed:
            async with self._locked_connections(user_id) as connections:
                for name, entry in user_managed.items():
                    connection = connections.get(name)
                    if connection is not None and connection.applies_to(entry.server):
                        connections[name] = connection.model_copy(
                            update={'state': state}
                        )
                        written.append(name)
        return written
