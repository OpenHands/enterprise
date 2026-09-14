"""Concrete app services whose injected collaborators are unittest mocks."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx

from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    LiveStatusAppConversationService,
)
from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService


@dataclass
class MockedConversationService(LiveStatusAppConversationService):
    user_context: AsyncMock
    app_conversation_info_service: AsyncMock
    app_conversation_start_task_service: AsyncMock
    event_callback_service: AsyncMock
    event_service: AsyncMock
    sandbox_service: AsyncMock
    sandbox_spec_service: AsyncMock
    jwt_service: MagicMock
    pending_message_service: AsyncMock


@dataclass
class MockedRemoteService(RemoteSandboxService):
    user_context: AsyncMock
    httpx_client: AsyncMock
    db_session: Mock


def make_conversation_service(
    user_context: AsyncMock | None = None,
    httpx_client: httpx.AsyncClient | None = None,
) -> MockedConversationService:
    return MockedConversationService(
        init_git_in_empty_workspace=True,
        user_context=user_context or AsyncMock(),
        app_conversation_info_service=AsyncMock(),
        app_conversation_start_task_service=AsyncMock(),
        event_callback_service=AsyncMock(),
        event_service=AsyncMock(),
        sandbox_service=AsyncMock(),
        sandbox_spec_service=AsyncMock(),
        jwt_service=MagicMock(),
        pending_message_service=AsyncMock(),
        sandbox_startup_timeout=30,
        sandbox_startup_poll_frequency=1,
        max_num_conversations_per_sandbox=3,
        httpx_client=httpx_client or AsyncMock(),
        web_url='https://app.example',
        openhands_provider_base_url=None,
        access_token_hard_timeout=None,
    )
