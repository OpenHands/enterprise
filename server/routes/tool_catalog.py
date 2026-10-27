"""Tool catalog for configuring cloud agent profiles."""

from typing import Any

from fastapi import APIRouter, Depends

import openhands.tools.task  # noqa: F401
from openhands.sdk.tool import registry
from openhands.tools.preset.default import register_default_tools
from openhands.tools.preset.gemini import register_gemini_tools
from openhands.tools.preset.planning import register_planning_tools
from server.auth.authorization import Permission, require_permission

router = APIRouter(prefix='/api/tools', tags=['Tools'])
register_default_tools(enable_browser=True)
register_gemini_tools(enable_browser=True)
register_planning_tools()


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
