"""Tool catalog for configuring cloud agent profiles."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from openhands.sdk.tool import registry
from server.auth.authorization import Permission, require_permission

router = APIRouter(prefix='/api/tools', tags=['Tools'])


@router.get('/catalog')
async def get_tool_catalog(
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> dict[str, list[dict[str, Any]]]:
    """List the tools a cloud agent profile can select."""
    # SDK releases before the tool catalog lack it; clients read 404 as "no picker".
    list_tool_catalog = getattr(registry, 'list_tool_catalog', None)
    if list_tool_catalog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Registers the same tool presets as the sandbox agent-server.
    import openhands.agent_server.tool_router  # noqa: F401

    # Cloud sandboxes run every registered tool, the browser included.
    return {
        'tools': [
            entry.model_copy(update={'usable': True}).model_dump(mode='json')
            for entry in list_tool_catalog()
        ]
    }
