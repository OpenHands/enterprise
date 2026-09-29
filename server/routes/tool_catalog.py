"""Tool catalog for configuring cloud agent profiles."""

from typing import Any

from fastapi import APIRouter, Depends

from openhands.sdk.tool import registry
from server.auth.authorization import Permission, require_permission

router = APIRouter(prefix='/api/tools', tags=['Tools'])


@router.get('/catalog')
async def get_tool_catalog(
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> dict[str, list[dict[str, Any]]]:
    """List the tools a cloud agent profile can select."""
    return {
        'tools': [
            entry.model_dump(mode='json')
            for entry in registry.list_tool_catalog(check_usable=False)
        ]
    }
