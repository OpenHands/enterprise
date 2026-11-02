"""Store for organization-scoped MCP server listings (OHE-3091).

Listings are usable by every member of the org in conversations but can only
be created, edited and deleted by Admins and Owners (``MANAGE_ORG_MCP``).
Mutations serialize on a ``SELECT ... FOR UPDATE`` of the org row, the same
lock the org Agent Profile and LLM profile routers take, and write the whole
collection back onto ``org.mcp_servers``.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from storage.agent_profile_resolution import load_agent_profiles
from storage.database import a_session_maker
from storage.org import Org
from storage.org_mcp_servers import (
    OrgMcpServer,
    dump_org_mcp_servers,
    load_org_mcp_servers,
)


class OrgMcpServersOrgMissingError(Exception):
    """Raised when the organization row does not exist."""


class OrgMcpServerNotFoundError(Exception):
    """Raised when no listing of that name exists."""


class OrgMcpServerAlreadyExistsError(Exception):
    """Raised when creating a listing whose name is taken."""


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
