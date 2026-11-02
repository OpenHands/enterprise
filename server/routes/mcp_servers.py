"""Member-scoped MCP mutations that must preserve stored credentials."""

from copy import deepcopy
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from openhands.app_server.settings.settings_models import _apply_mcp_config_update
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import EFFECTIVE_ORG_ID
from storage.database import a_session_maker
from storage.mcp_config import coerce_persisted_mcp_config, serialize_mcp_config
from storage.org_member import OrgMember

router = APIRouter(prefix='/api/v1/mcp/servers', tags=['MCP'])


class RenameMcpServerRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
    server: dict[str, Any] | None = None


@router.post('/{name}/rename')
async def rename_mcp_server(
    name: str,
    body: RenameMcpServerRequest,
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> dict[str, str]:
    async with a_session_maker() as session:
        member = await session.scalar(
            select(OrgMember)
            .where(
                OrgMember.org_id == effective_org_id,
                OrgMember.user_id == UUID(user_id),
            )
            .with_for_update()
        )
        if member is None:
            raise HTTPException(404, 'Organization membership not found')
        try:
            servers = coerce_persisted_mcp_config(member.effective_mcp_config or {})
            if name not in servers:
                raise HTTPException(404, 'MCP server not found')
            if any(
                key != name and key.casefold() == body.new_name.casefold()
                for key in servers
            ):
                raise HTTPException(409, 'An MCP server with that name already exists')
            if body.server is not None:
                servers[name] = _apply_mcp_config_update(
                    {name: body.server}, {name: servers[name]}
                )[name]
            servers[body.new_name] = servers.pop(name)
            member.mcp_config = serialize_mcp_config(servers)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, 'Invalid MCP server configuration') from exc

        # Rewrite only references, retaining unknown profile fields and stable IDs.
        profiles = deepcopy(member.agent_profiles)
        if profiles and name != body.new_name:
            for profile in profiles.get('profiles', {}).values():
                refs = profile.get('mcp_server_refs')
                if isinstance(refs, list) and name in refs:
                    profile['mcp_server_refs'] = [
                        body.new_name if ref == name else ref for ref in refs
                    ]
                    profile['revision'] = (profile.get('revision') or 0) + 1
            member.agent_profiles = profiles
        await session.commit()
    return {'name': body.new_name}
