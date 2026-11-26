"""Team settings scope: explicit identity, explicit operations.

The team row owns the settings document and its revision. Every entry point
takes the tenant explicitly (``org_id``) together with the team id, and both
reads and writes require the acting ``user_id`` to have an explicit membership
row. There is no "current org"/"current team" lookup and no way to skip the
membership check, so a caller cannot reach another tenant's settings by
passing an id it happens to know.

Mutation accepts the canonical operations from
``openhands.app_server.settings.scoped``. The caller supplies the validator
for the resolved document (the parent settings layer owns SDK validation), so
this module never interprets or rewrites settings semantics itself.

Writes run inside the caller's transaction (``flush`` only); the caller owns
commit and rollback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.settings.scoped import Operation
from storage.settings_document import save_scope
from storage.team import Team
from storage.team_member import TeamMember


class TeamSettingsError(Exception):
    """Base class for team settings persistence failures."""


class TeamMembershipError(TeamSettingsError):
    """The acting user has no explicit membership in the requested team."""


class TeamSettingsRevisionConflict(TeamSettingsError):
    """The caller edited a revision that is no longer current."""


async def load_team_settings(
    session: AsyncSession,
    *,
    org_id: UUID,
    team_id: UUID,
    user_id: UUID,
    for_update: bool = False,
) -> Team:
    query = (
        select(Team)
        .join(TeamMember)
        .where(Team.org_id == org_id, Team.id == team_id, TeamMember.user_id == user_id)
    )
    if for_update:
        query = query.with_for_update(of=Team)
    team = (await session.scalars(query)).one_or_none()
    if team is None:
        raise TeamMembershipError(
            'Acting user is not a member of the requested team in this tenant'
        )
    return team


async def mutate_team_settings(
    session: AsyncSession,
    *,
    org_id: UUID,
    team_id: UUID,
    user_id: UUID,
    operations: Iterable[Operation],
    parent: dict[str, Any] | None = None,
    validate: Callable[[dict[str, Any]], None] | None = None,
    expected_revision: int | None = None,
) -> Team:
    """Apply explicit operations to the team's sparse document.

    ``operations`` are ``Set``/``Inherit`` values from the canonical scoped
    API; this function never diffs two effective documents and never writes an
    inherited value. ``validate`` receives the edited *local* document; the
    caller overlays its parent layers on top and runs SDK validation there, so
    policy and credential handling stay in the parent settings layer.

    ``expected_revision`` is the stale-edit guard: ``None`` skips the check.
    """
    team = await load_team_settings(
        session, org_id=org_id, team_id=team_id, user_id=user_id, for_update=True
    )
    if expected_revision is not None and team.revision != expected_revision:
        raise TeamSettingsRevisionConflict(
            f'Expected revision {expected_revision} but found {team.revision}'
        )

    previous = team.settings
    save_scope(team, operations, parent or {}, validate=validate)
    if team.settings == previous:
        return team
    team.revision = team.revision + 1
    team.updated_by = user_id
    await session.flush()
    return team
