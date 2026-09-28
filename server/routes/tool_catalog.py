"""Tool catalog for configuring cloud agent profiles."""

import inspect
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
    list_tool_catalog = getattr(registry, 'list_tool_catalog', None)
    if list_tool_catalog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    import openhands.agent_server.tool_router  # noqa: F401

    # The sandbox image is built from the same SDK release, so every tool
    # registered here resolves there; usability can't be probed from here.
    if 'check_usable' in inspect.signature(list_tool_catalog).parameters:
        entries = list_tool_catalog(check_usable=False)
    else:
        entries = list_tool_catalog()
    return {
        'tools': [
            entry.model_copy(update={'usable': True}).model_dump(mode='json')
            for entry in entries
        ]
    }
