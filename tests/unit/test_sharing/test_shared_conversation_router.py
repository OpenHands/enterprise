"""Route-level tests for listing the conversations shared with an org.

The point of interest is org scoping: the route must list for the request's
*effective* org (``X-Org-Id`` / API-key binding, resolved by
``EFFECTIVE_ORG_ID``) and never for an org named by the caller.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.auth.org_context import resolve_effective_org_id
from server.sharing.shared_conversation_info_service import (
    SharedConversationInfoService,
)
from server.sharing.shared_conversation_models import (
    SharedConversation,
    SharedConversationPage,
)
from server.sharing.shared_conversation_router import (
    router,
    shared_conversation_info_service_dependency,
)

EFFECTIVE_ORG = uuid.uuid4()


@pytest.mark.asyncio
async def test_search_lists_the_conversations_shared_with_the_effective_org():
    # Arrange
    conversation = SharedConversation(
        created_by_user_id=str(uuid.uuid4()),
        sandbox_id='sandbox',
        title='Shared with the org',
    )
    service = AsyncMock(spec=SharedConversationInfoService)
    service.search_shared_conversation_info.return_value = SharedConversationPage(
        items=[conversation], next_page_id='4'
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_effective_org_id] = lambda: EFFECTIVE_ORG
    app.dependency_overrides[shared_conversation_info_service_dependency.dependency] = (
        lambda: service
    )

    # Act
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get(
            '/api/shared-conversations/search', params={'page_id': '2', 'limit': 2}
        )

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert [uuid.UUID(item['id']) for item in body['items']] == [conversation.id]
    assert body['next_page_id'] == '4'
    service.search_shared_conversation_info.assert_awaited_once_with(
        org_id=EFFECTIVE_ORG, page_id='2', limit=2
    )
