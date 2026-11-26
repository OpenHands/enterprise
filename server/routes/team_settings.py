"""Manage a team's sparse settings, not the settings resolved for any member."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from server.auth.authorization import Permission, require_permission
from server.auth.org_context import REJECT_X_ORG_ID_PATH_MISMATCH
from storage.database import a_session_maker
from storage.org import Org
from storage.team_settings_store import (
    TeamMembershipError,
    TeamSettingsRevisionConflict,
    load_team_settings,
    mutate_team_settings,
)

router = APIRouter(
    prefix='/api/organizations/{org_id}/teams/{team_id}/settings',
    tags=['Orgs'],
    dependencies=[REJECT_X_ORG_ID_PATH_MISMATCH],
)


class TeamEdit(BaseModel):
    revision: int = Field(ge=0)
    parent_updated_at: datetime
    model_config = ConfigDict(extra='forbid')
    settings: dict[str, dict[str, int]] = Field(min_length=1)


# Team admins can tune execution limits, not change provider endpoints, credentials,
# agent implementations, or organization policy by replacing a parent object.
_TEAM_WRITABLE_PATHS = {
    ('agent_settings', 'tool_concurrency_limit'),
    ('conversation_settings', 'max_iterations'),
}


@router.get('')
async def get_team_settings(
    org_id: UUID,
    team_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
):
    async with a_session_maker() as session:
        try:
            team = await load_team_settings(
                session, org_id=org_id, team_id=team_id, user_id=UUID(user_id)
            )
        except TeamMembershipError:
            raise HTTPException(403, 'No access to this team') from None
        org = await session.get(Org, org_id)
        if org is None:
            raise HTTPException(404, 'Organization not found')
        return {
            'settings': team.settings,
            'revision': team.revision,
            'parent_updated_at': org.updated_at,
        }


@router.patch('')
async def edit_team_settings(
    org_id: UUID,
    team_id: UUID,
    patch: TeamEdit,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
):
    paths = {
        (block, name) for block, fields in patch.settings.items() for name in fields
    }
    if not paths or paths - _TEAM_WRITABLE_PATHS:
        raise HTTPException(422, 'Path is not team-editable')
    async with a_session_maker() as session, session.begin():
        org = await session.get(Org, org_id, with_for_update=True)
        if org is None:
            raise HTTPException(404, 'Organization not found')

        try:
            team = await mutate_team_settings(
                session,
                org_id=org_id,
                team_id=team_id,
                user_id=UUID(user_id),
                submitted=patch.settings,
                parent=org.settings,
                expected_revision=patch.revision,
                parent_changed=org.updated_at != patch.parent_updated_at,
            )
        except TeamMembershipError:
            raise HTTPException(403, 'No access to this team') from None
        except TeamSettingsRevisionConflict:
            raise HTTPException(
                409, 'Team settings changed; reload before saving'
            ) from None
        except ValueError:
            raise HTTPException(422, 'Invalid team settings') from None
        return {
            'settings': team.settings,
            'revision': team.revision,
            'parent_updated_at': org.updated_at,
        }
