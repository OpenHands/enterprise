"""Manage a team's sparse settings, not the settings resolved for any member."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openhands.app_server.settings.scoped import Inherit, Set, resolve
from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import REJECT_X_ORG_ID_PATH_MISMATCH
from storage.database import a_session_maker
from storage.org import Org
from storage.org_store import OrgStore
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


class SetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['set']
    path: tuple[str, ...]
    value: JsonValue


class InheritRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['inherit']
    path: tuple[str, ...]


class TeamEdit(BaseModel):
    revision: int = Field(ge=0)
    operations: list[
        Annotated[SetRequest | InheritRequest, Field(discriminator='op')]
    ] = Field(min_length=1, max_length=100)


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
        return {'settings': team.settings, 'revision': team.revision}


@router.patch('')
async def edit_team_settings(
    org_id: UUID,
    team_id: UUID,
    patch: TeamEdit,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
):
    if any(op.path not in _TEAM_WRITABLE_PATHS for op in patch.operations):
        raise HTTPException(422, 'Path is not team-editable')
    operations = [
        Set(op.path, op.value) if isinstance(op, SetRequest) else Inherit(op.path)
        for op in patch.operations
    ]
    async with a_session_maker() as session, session.begin():
        org = await session.get(Org, org_id, with_for_update=True)
        if org is None:
            raise HTTPException(404, 'Organization not found')

        def validate(doc):
            effective = resolve(org.settings, doc)
            agent = OrgStore.get_agent_settings_from_org(org)
            if doc.get('agent_settings') and not isinstance(
                agent, OpenHandsAgentSettings
            ):
                raise ValueError('Agent variant does not support these limits')
            if isinstance(agent, OpenHandsAgentSettings):
                OpenHandsAgentSettings.model_validate(
                    resolve(
                        agent.model_dump(mode='json'), doc.get('agent_settings', {})
                    )
                )
            ConversationSettings.from_persisted(
                effective.get('conversation_settings', {})
            )

        try:
            team = await mutate_team_settings(
                session,
                org_id=org_id,
                team_id=team_id,
                user_id=UUID(user_id),
                operations=operations,
                expected_revision=patch.revision,
                validate=validate,
            )
        except TeamMembershipError:
            raise HTTPException(403, 'No access to this team') from None
        except TeamSettingsRevisionConflict:
            raise HTTPException(
                409, 'Team settings changed; reload before saving'
            ) from None
        except ValueError:
            raise HTTPException(422, 'Invalid team settings') from None
        return {'settings': team.settings, 'revision': team.revision}
