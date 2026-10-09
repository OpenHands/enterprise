"""Exercise router/service quota ownership against the migrated PostgreSQL schema.

Authentication and sandbox startup are stubbed, and analytics is disabled: reservation, release,
start-task persistence, and background consumption all use production code.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException, Request

from openhands.app_server.app_conversation import app_conversation_router as router
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    LiveStatusAppConversationService,
)
from openhands.app_server.app_conversation.sql_app_conversation_start_task_service import (
    SQLAppConversationStartTaskService,
)
from openhands.app_server.config import get_global_config
from openhands.app_server.config_api.config_models import AppMode
from openhands.app_server.shared import server_config
from openhands.app_server.user.user_models import UserInfo
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
)


@dataclass
class QuotaUserContext:
    user_id: str
    org_id: UUID

    async def get_user_id(self):
        return self.user_id

    async def get_effective_org_id(self):
        return self.org_id

    async def get_user_info(self, **kwargs):
        return UserInfo(id=self.user_id)


@pytest.fixture
async def quota_start(app_db_session, async_session_maker, create_user, monkeypatch):
    user = create_user(daily_conversation_limit=10)
    user_context = QuotaUserContext(str(user.id), user.current_org_id)
    monkeypatch.setattr(server_config, 'app_mode', AppMode.SAAS)
    monkeypatch.setattr(router, 'get_analytics_service', lambda: None)

    async def start(endpoint, outcome=AppConversationStartTaskStatus.READY):
        session = async_session_maker()
        client = httpx.AsyncClient()
        service = LiveStatusAppConversationService(
            init_git_in_empty_workspace=False,
            user_context=user_context,
            app_conversation_info_service=None,
            app_conversation_start_task_service=SQLAppConversationStartTaskService(
                session, str(user.id)
            ),
            event_callback_service=None,
            event_service=None,
            sandbox_service=None,
            sandbox_spec_service=None,
            jwt_service=None,
            pending_message_service=None,
            sandbox_startup_timeout=1,
            sandbox_startup_poll_frequency=1,
            max_num_conversations_per_sandbox=1,
            httpx_client=client,
            web_url=None,
            openhands_provider_base_url=None,
            access_token_hard_timeout=None,
        )

        async def sandbox_start(request):
            task = AppConversationStartTask(
                created_by_user_id=str(user.id), request=request
            )
            if outcome == 'raise-before':
                raise RuntimeError('startup failed')
            yield task
            if outcome == 'raise-after':
                raise RuntimeError('startup failed')
            task.status = outcome
            yield task

        monkeypatch.setattr(service, '_start_app_conversation', sandbox_start)

        @asynccontextmanager
        async def service_context(state):
            assert state.user_context is user_context
            if outcome == 'injection-failure':
                raise RuntimeError('startup failed')
            yield service

        monkeypatch.setattr(router, 'get_app_conversation_service', service_context)
        request = AppConversationStartRequest()
        try:
            if endpoint == 'stream':
                response = await router.stream_app_conversation_start(
                    request, user_context, None
                )
                if outcome == 'close-before-start':
                    assert await anext(response.body_iterator) == '[\n'
                    await response.body_iterator.aclose()
                    return
                tasks = json.loads(
                    ''.join([chunk async for chunk in response.body_iterator])
                )
                assert tasks[-1]['status'] == outcome.value
            elif endpoint == 'post':
                before = asyncio.all_tasks()
                task = await router.start_app_conversation(
                    Request({'type': 'http'}),
                    request,
                    user_context,
                    None,
                    session,
                    client,
                    service,
                )
                assert task.created_by_user_id == str(user.id)
                background = asyncio.all_tasks() - before
                assert len(background) == 1
                await asyncio.gather(*background)
            else:
                tasks = [task async for task in service.start_app_conversation(request)]
                assert tasks[-1].status == outcome
        finally:
            await session.close()
            await client.aclose()

    try:
        yield user, start
    finally:
        await get_global_config().db_session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['post', 'stream', 'service'])
async def test_successful_start_consumes_one_slot(
    quota_start, async_session_maker, endpoint
):
    user, start = quota_start
    await start(endpoint)
    async with async_session_maker() as session:
        quota = await DailyConversationQuotaService(session).get_status(str(user.id))
    assert quota.used_today == 1
    assert quota.remaining == 9


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['post', 'stream', 'service'])
@pytest.mark.parametrize(
    'outcome', [AppConversationStartTaskStatus.ERROR, 'raise-before', 'raise-after']
)
async def test_failed_start_preserves_existing_usage(
    quota_start, async_session_maker, endpoint, outcome
):
    user, start = quota_start
    async with async_session_maker() as session:
        await DailyConversationQuotaService(session).reserve(str(user.id))
    if isinstance(outcome, AppConversationStartTaskStatus):
        await start(endpoint, outcome)
    else:
        with pytest.raises(RuntimeError, match='startup failed'):
            await start(endpoint, outcome)
    async with async_session_maker() as session:
        quota = await DailyConversationQuotaService(session).get_status(str(user.id))
    assert quota.used_today == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['post', 'stream', 'service'])
async def test_last_slot_is_usable_and_next_start_is_rejected(
    quota_start, async_session_maker, endpoint
):
    user, start = quota_start
    async with async_session_maker() as session:
        quota_service = DailyConversationQuotaService(session)
        for _ in range(9):
            await quota_service.reserve(str(user.id))
    await start(endpoint)
    with pytest.raises(HTTPException) as exc:
        await start(endpoint)
    assert exc.value.status_code == 429
    async with async_session_maker() as session:
        quota = await DailyConversationQuotaService(session).get_status(str(user.id))
    assert quota.used_today == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['injection-failure', 'close-before-start'])
async def test_stream_failure_before_handoff_releases_reservation(
    quota_start, async_session_maker, outcome
):
    user, start = quota_start
    async with async_session_maker() as session:
        await DailyConversationQuotaService(session).reserve(str(user.id))
    if outcome == 'injection-failure':
        with pytest.raises(RuntimeError, match='startup failed'):
            await start('stream', outcome)
    else:
        await start('stream', outcome)
    async with async_session_maker() as session:
        quota = await DailyConversationQuotaService(session).get_status(str(user.id))
    assert quota.used_today == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['post', 'stream', 'service'])
async def test_unlimited_start_does_not_change_usage(
    quota_start, async_session_maker, endpoint
):
    user, start = quota_start
    async with async_session_maker() as session:
        await DailyConversationQuotaService(session).reserve(str(user.id))
        user.daily_conversation_limit = -1
        await session.merge(user)
        await session.commit()
    await start(endpoint)
    await start(endpoint, AppConversationStartTaskStatus.ERROR)
    async with async_session_maker() as session:
        quota = await DailyConversationQuotaService(session).get_status(str(user.id))
    assert quota.used_today == 1
    assert quota.daily_limit is None
