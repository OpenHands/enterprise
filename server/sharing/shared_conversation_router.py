"""Shared Conversation router for OpenHands Server."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from server.auth.org_context import EFFECTIVE_ORG_ID
from server.sharing.shared_conversation_info_service import (
    SharedConversationInfoService,
)
from server.sharing.shared_conversation_models import (
    SharedConversation,
    SharedConversationPage,
)
from server.sharing.sql_shared_conversation_info_service import (
    SQLSharedConversationInfoServiceInjector,
)

router = APIRouter(prefix='/api/shared-conversations', tags=['Sharing'])
shared_conversation_info_service_dependency = Depends(
    SQLSharedConversationInfoServiceInjector().depends
)


# Read methods
#
# Authentication is optional on the batch lookup by known IDs, so that share
# links of the form /shared/conversations/<id> can be viewed without auth.
# Anonymous callers see public conversations shared from a personal
# workspace; authenticated callers additionally see the public and
# automation-triggered conversations of the orgs they belong to.
#
# The search requires authentication and only lists the conversations shared
# with the caller's effective org, to members of that org. Listing or
# enumerating shared conversations is intentionally not exposed beyond that.


@router.get('/search')
async def search_shared_conversations(
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title='The max number of results in the page',
            gt=0,
            le=100,
        ),
    ] = 100,
    org_id: UUID = EFFECTIVE_ORG_ID,
    shared_conversation_service: SharedConversationInfoService = shared_conversation_info_service_dependency,
) -> SharedConversationPage:
    """Search / List the conversations shared with the caller's effective org."""
    return await shared_conversation_service.search_shared_conversation_info(
        org_id=org_id,
        page_id=page_id,
        limit=limit,
    )


@router.get('')
async def batch_get_shared_conversations(
    ids: Annotated[list[str], Query()],
    shared_conversation_service: SharedConversationInfoService = shared_conversation_info_service_dependency,
) -> list[SharedConversation | None]:
    """Get a batch of shared conversations given their ids. Return None for any missing or non-shared."""
    if len(ids) > 100:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot request more than 100 conversations at once, got {len(ids)}',
        )
    uuids = [UUID(id_) for id_ in ids]
    shared_conversation_info = (
        await shared_conversation_service.batch_get_shared_conversation_info(uuids)
    )
    return shared_conversation_info
