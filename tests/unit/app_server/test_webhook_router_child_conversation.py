"""Tests for the sandbox-authenticated child conversation launcher.

``POST /api/v1/webhooks/conversations/{id}/children`` is the Cloud launcher
behind the ``start_child_conversation`` tool: one call must provision exactly
one child through the normal app-conversation lifecycle, linked to its parent.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from openhands.agent_server.models import StartChildConversationRequest
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from openhands.app_server.errors import AuthError
from openhands.app_server.event_callback.webhook_router import (
    start_child_conversation,
)
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user_auth.default_user_auth import DefaultUserAuth

ROUTER = 'openhands.app_server.event_callback.webhook_router'
CHILD_ID = uuid4()
READY_SEQUENCE = [
    (AppConversationStartTaskStatus.WORKING, None),
    (AppConversationStartTaskStatus.READY, None),
]


class _OrgAwareUserAuth(DefaultUserAuth):
    """DefaultUserAuth plus the SaaS org-override hook, to observe scoping."""

    override_org_id: UUID | None = None

    def set_effective_org_id_override(self, org_id: UUID | None) -> None:
        self.override_org_id = org_id


class _FakeAppConversationService:
    """Records start requests and replays a scripted start-task sequence."""

    def __init__(self, statuses):
        self.statuses = statuses
        self.requests = []

    async def start_app_conversation(self, request):
        self.requests.append(request)
        for status, detail in self.statuses:
            task = AppConversationStartTask(
                created_by_user_id='user_123',
                request=request,
                status=status,
                detail=detail,
            )
            if status == AppConversationStartTaskStatus.READY:
                task.app_conversation_id = CHILD_ID
            yield task


@pytest.fixture
def sandbox_record() -> SandboxRecord:
    return SandboxRecord(id='sandbox_123', created_by_user_id='user_123')


@pytest.fixture
def parent() -> AppConversationInfo:
    return AppConversationInfo(
        id=uuid4(),
        title='Parent',
        sandbox_id='sandbox_123',
        created_by_user_id='user_123',
        selected_repository='org/repo',
    )


@pytest.fixture
def user_auth() -> _OrgAwareUserAuth:
    return _OrgAwareUserAuth()


@asynccontextmanager
async def _launcher(parent, app_service, user_auth, org_id=None):
    """Wire the endpoint's collaborators to in-memory fakes."""

    @asynccontextmanager
    async def info_service_ctx(state, request=None):
        service = AsyncMock()
        service.get_app_conversation_info.return_value = parent
        yield service

    @asynccontextmanager
    async def app_service_ctx(state, request=None):
        yield app_service

    with (
        patch(f'{ROUTER}.get_app_conversation_info_service', info_service_ctx),
        patch(f'{ROUTER}.get_app_conversation_service', app_service_ctx),
        patch(
            f'{ROUTER}._resolve_user_context',
            AsyncMock(return_value=AuthUserContext(user_auth=user_auth)),
        ),
        patch(f'{ROUTER}._resolve_conversation_org_id', AsyncMock(return_value=org_id)),
        patch(
            f'{ROUTER}.get_global_config',
            MagicMock(return_value=SimpleNamespace(web_url='https://app.example')),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_provisions_exactly_one_child_linked_to_parent(
    sandbox_record, parent, user_auth
):
    # Arrange
    app_service = _FakeAppConversationService(READY_SEQUENCE)
    request = StartChildConversationRequest(task='Write the docs', title='Docs')

    # Act
    async with _launcher(parent, app_service, user_auth):
        await start_child_conversation(
            parent.id, request, sandbox_record=sandbox_record
        )

    # Assert
    assert len(app_service.requests) == 1
    start_request = app_service.requests[0]
    assert start_request.parent_conversation_id == parent.id
    assert start_request.title == 'Docs'
    assert start_request.initial_message is not None
    assert start_request.initial_message.content[0].text == 'Write the docs'


@pytest.mark.asyncio
async def test_scopes_launch_to_parent_organization(sandbox_record, parent, user_auth):
    # Arrange
    app_service = _FakeAppConversationService(READY_SEQUENCE)
    org_id = uuid4()

    # Act
    async with _launcher(parent, app_service, user_auth, org_id=org_id):
        await start_child_conversation(
            parent.id,
            StartChildConversationRequest(task='Write the docs'),
            sandbox_record=sandbox_record,
        )

    # Assert
    assert user_auth.override_org_id == org_id


@pytest.mark.asyncio
async def test_returns_child_identity_status_and_url(sandbox_record, parent, user_auth):
    # Arrange
    app_service = _FakeAppConversationService(READY_SEQUENCE)

    # Act
    async with _launcher(parent, app_service, user_auth):
        response = await start_child_conversation(
            parent.id,
            StartChildConversationRequest(task='Write the docs', title='Docs'),
            sandbox_record=sandbox_record,
        )

    # Assert
    assert response.conversation_id == CHILD_ID
    assert response.parent_conversation_id == parent.id
    assert response.status == 'READY'
    assert response.title == 'Docs'
    assert response.url == f'https://app.example/conversations/{CHILD_ID}'


@pytest.mark.asyncio
async def test_missing_parent_is_404(sandbox_record, user_auth):
    # Arrange
    app_service = _FakeAppConversationService(READY_SEQUENCE)

    # Act / Assert
    async with _launcher(None, app_service, user_auth):
        with pytest.raises(HTTPException) as exc_info:
            await start_child_conversation(
                uuid4(),
                StartChildConversationRequest(task='anything'),
                sandbox_record=sandbox_record,
            )

    assert exc_info.value.status_code == 404
    assert app_service.requests == []


@pytest.mark.asyncio
async def test_parent_from_another_sandbox_is_rejected(parent, user_auth):
    # Arrange
    app_service = _FakeAppConversationService(READY_SEQUENCE)
    other_sandbox = SandboxRecord(id='sandbox_999', created_by_user_id='user_123')

    # Act / Assert
    async with _launcher(parent, app_service, user_auth):
        with pytest.raises(AuthError):
            await start_child_conversation(
                parent.id,
                StartChildConversationRequest(task='anything'),
                sandbox_record=other_sandbox,
            )

    assert app_service.requests == []


@pytest.mark.asyncio
async def test_failed_start_is_surfaced_with_detail(sandbox_record, parent, user_auth):
    # Arrange
    app_service = _FakeAppConversationService(
        [
            (AppConversationStartTaskStatus.WORKING, None),
            (AppConversationStartTaskStatus.ERROR, 'sandbox exploded'),
        ]
    )

    # Act / Assert
    async with _launcher(parent, app_service, user_auth):
        with pytest.raises(HTTPException) as exc_info:
            await start_child_conversation(
                parent.id,
                StartChildConversationRequest(task='anything'),
                sandbox_record=sandbox_record,
            )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == 'sandbox exploded'
