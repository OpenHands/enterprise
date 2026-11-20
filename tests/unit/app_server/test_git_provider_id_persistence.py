"""``git_provider_id`` is stored with a conversation and with its start task.

Runs against the test database, where ``auth_providers`` is a real table the
column points at.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openhands.agent_server.models import ConversationInfo
from openhands.app_server.app_conversation.additional_repositories import (
    AdditionalRepository,
    AdditionalRepositoryRequest,
    AdditionalRepositoryStatus,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationUpdateRequest,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
)
from openhands.app_server.app_conversation.sql_app_conversation_start_task_service import (
    SQLAppConversationStartTaskService,
)
from openhands.app_server.event_callback.webhook_router import on_conversation_update
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext
from openhands.sdk.conversation import ConversationExecutionStatus
from storage.auth_provider import AuthProvider


@pytest.fixture
async def session(async_engine):
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with maker() as session:
        yield session


@pytest.fixture
def info_service(session):
    return SQLAppConversationInfoService(
        db_session=session, user_context=SpecifyUserContext(user_id='user_123')
    )


@pytest.fixture
def task_service(session):
    return SQLAppConversationStartTaskService(session=session)


def _info(git_provider_id: int | None, **kwargs) -> AppConversationInfo:
    return AppConversationInfo(
        id=uuid4(),
        title='A conversation',
        sandbox_id='sandbox_123',
        created_by_user_id='user_123',
        selected_repository='tofarr/servey',
        git_provider=ProviderType.GITLAB,
        git_provider_id=git_provider_id,
        **kwargs,
    )


def _task(git_provider_id: int | None) -> AppConversationStartTask:
    return AppConversationStartTask(
        created_by_user_id='user_123',
        request=AppConversationStartRequest(
            selected_repository='tofarr/servey',
            git_provider=ProviderType.GITLAB,
            git_provider_id=git_provider_id,
        ),
        git_provider_id=git_provider_id,
    )


async def _delete_provider(session: AsyncSession, provider: AuthProvider) -> None:
    await session.execute(delete(AuthProvider).where(AuthProvider.id == provider.id))
    await session.commit()


@pytest.mark.asyncio
async def test_a_conversation_keeps_its_git_provider_id(
    info_service, create_git_provider
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    info = _info(gitlab.id)

    await info_service.save_app_conversation_info(info)
    loaded = await info_service.get_app_conversation_info(info.id)

    assert loaded is not None
    assert loaded.git_provider_id == gitlab.id
    assert loaded.git_provider == ProviderType.GITLAB


@pytest.mark.asyncio
async def test_a_conversation_without_an_id_stays_without_one(info_service):
    info = _info(None)

    await info_service.save_app_conversation_info(info)
    loaded = await info_service.get_app_conversation_info(info.id)

    assert loaded is not None
    assert loaded.git_provider_id is None


@pytest.mark.asyncio
async def test_an_id_can_be_changed_and_cleared_by_a_later_save(
    info_service, create_git_provider
):
    first = await create_git_provider(ProviderType.GITLAB, host='https://a.example.com')
    second = await create_git_provider(
        ProviderType.GITLAB, host='https://b.example.com'
    )
    info = _info(first.id)
    await info_service.save_app_conversation_info(info)

    info.git_provider_id = second.id
    await info_service.save_app_conversation_info(info)
    assert (
        await info_service.get_app_conversation_info(info.id)
    ).git_provider_id == second.id

    info.git_provider_id = None
    await info_service.save_app_conversation_info(info)
    assert (
        await info_service.get_app_conversation_info(info.id)
    ).git_provider_id is None


@pytest.mark.asyncio
async def test_additional_repositories_keep_their_git_provider_id(
    info_service, create_git_provider
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    info = _info(
        None,
        additional_repositories=[
            AdditionalRepository(
                repository='tofarr/lib',
                git_provider=ProviderType.GITLAB,
                git_provider_id=gitlab.id,
                folder='lib',
                path='/workspace/project/lib',
                status=AdditionalRepositoryStatus.CLONED,
            )
        ],
    )

    await info_service.save_app_conversation_info(info)
    loaded = await info_service.get_app_conversation_info(info.id)

    assert loaded is not None
    assert [r.git_provider_id for r in loaded.additional_repositories] == [gitlab.id]


@pytest.mark.asyncio
async def test_a_start_task_keeps_its_git_provider_id(
    task_service, create_git_provider
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    task = _task(gitlab.id)

    await task_service.save_app_conversation_start_task(task)
    loaded = await task_service.get_app_conversation_start_task(task.id)

    assert loaded is not None
    assert loaded.git_provider_id == gitlab.id
    assert loaded.request.git_provider_id == gitlab.id


@pytest.mark.asyncio
async def test_a_start_task_without_an_id_stays_without_one(task_service):
    task = _task(None)

    await task_service.save_app_conversation_start_task(task)
    loaded = await task_service.get_app_conversation_start_task(task.id)

    assert loaded is not None
    assert loaded.git_provider_id is None


@pytest.mark.asyncio
async def test_removing_the_provider_row_clears_the_id_and_keeps_the_conversation(
    session, info_service, task_service, create_git_provider
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    info = _info(gitlab.id)
    task = _task(gitlab.id)
    await info_service.save_app_conversation_info(info)
    await task_service.save_app_conversation_start_task(task)

    await _delete_provider(session, gitlab)

    loaded = await info_service.get_app_conversation_info(info.id)
    assert loaded is not None
    assert loaded.git_provider_id is None
    assert loaded.selected_repository == 'tofarr/servey'
    loaded_task = await task_service.get_app_conversation_start_task(task.id)
    assert loaded_task is not None
    assert loaded_task.git_provider_id is None


@pytest.mark.asyncio
async def test_the_lifecycle_webhook_keeps_the_git_provider_id(
    info_service, create_git_provider
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    existing = _info(gitlab.id)
    await info_service.save_app_conversation_info(existing)
    conversation_info = MagicMock(spec=ConversationInfo)
    conversation_info.id = existing.id
    conversation_info.execution_status = ConversationExecutionStatus.RUNNING
    conversation_info.agent = MagicMock()
    conversation_info.agent.llm.model = 'gpt-4'
    conversation_info.stats = MagicMock()
    conversation_info.stats.get_combined_metrics.return_value = None
    conversation_info.tags = {}

    @asynccontextmanager
    async def event_callback_service(state, request=None):
        yield AsyncMock()

    with (
        patch(
            'openhands.app_server.event_callback.webhook_router.valid_conversation',
            return_value=await info_service.get_app_conversation_info(existing.id),
        ),
        patch(
            'openhands.app_server.event_callback.webhook_router.get_event_callback_service',
            event_callback_service,
        ),
    ):
        await on_conversation_update(
            conversation_info=conversation_info,
            sandbox_record=SandboxRecord(
                id='sandbox_123', created_by_user_id='user_123'
            ),
            app_conversation_info_service=info_service,
        )

    saved = await info_service.get_app_conversation_info(existing.id)
    assert saved is not None
    assert saved.git_provider_id == gitlab.id


def test_the_update_request_and_additional_repository_request_take_an_id():
    assert AppConversationUpdateRequest(git_provider_id=3).git_provider_id == 3
    assert AppConversationUpdateRequest().git_provider_id is None
    request = AdditionalRepositoryRequest(
        repository='tofarr/lib', git_provider=ProviderType.GITLAB, git_provider_id=4
    )
    assert request.git_provider_id == 4
