"""Runtime Images router for OpenHands App Server."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openhands.app_server.config import depends_sandbox_spec_service
from openhands.app_server.sandbox.sandbox_spec_models import (
    SandboxSpecInfo,
    SandboxSpecInfoPage,
)
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
)
from openhands.app_server.utils.dependencies import get_dependencies

# We use the get_dependencies method here to signal to the OpenAPI docs that this endpoint
# is protected. The actual protection is provided by SetAuthCookieMiddleware
# Sandboxes specs share a single immutable list for the server right now, but that is likely to
# change in the future
router = APIRouter(
    prefix='/sandbox-specs', tags=['Sandbox'], dependencies=get_dependencies()
)
sandbox_spec_service_dependency = depends_sandbox_spec_service()


class SandboxTemplateOption(BaseModel):
    id: str
    display_name: str


class SandboxSelectionOptions(BaseModel):
    enabled: bool
    items: list[SandboxTemplateOption]


@router.get('/selection')
async def get_sandbox_selection_options(
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
) -> SandboxSelectionOptions:
    """Organization-scoped launch choices, without runtime environment values."""
    if not sandbox_spec_service.selection_enabled:
        return SandboxSelectionOptions(enabled=False, items=[])
    items: list[SandboxTemplateOption] = []
    page_id = None
    while True:
        page = await sandbox_spec_service.search_sandbox_specs(page_id=page_id)
        items.extend(
            SandboxTemplateOption(id=spec.id, display_name=spec.display_name or spec.id)
            for spec in page.items
        )
        page_id = page.next_page_id
        if page_id is None:
            break
    return SandboxSelectionOptions(enabled=True, items=items)


# Read methods


@router.get('/search')
async def search_sandbox_specs(
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(title='The max number of results in the page', gt=0, le=100),
    ] = 100,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
) -> SandboxSpecInfoPage:
    """Search / List sandbox specs."""
    return await sandbox_spec_service.search_sandbox_specs(page_id=page_id, limit=limit)


@router.get('')
async def batch_get_sandbox_specs(
    id: Annotated[list[str], Query()],
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
) -> list[SandboxSpecInfo | None]:
    """Get a batch of sandbox specs given their ids, returning null for any missing."""
    if len(id) > 100:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot request more than 100 sandbox specs at once, got {len(id)}',
        )
    sandbox_specs = await sandbox_spec_service.batch_get_sandbox_specs(id)
    return sandbox_specs
