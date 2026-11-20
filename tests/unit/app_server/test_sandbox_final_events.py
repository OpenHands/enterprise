from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from openhands.app_server import config
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationInfoPage,
)
from openhands.app_server.event.google_cloud_event_service import (
    GoogleCloudEventService,
)
from openhands.app_server.sandbox import final_events
from openhands.app_server.user.specifiy_user_context import (
    USER_CONTEXT_ATTR,
    SandboxUserContext,
    SpecifyUserContext,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('gap', [False, True])
async def test_reconciliation_pins_snapshot_and_rejects_sequence_holes(gap):
    conversation_id = uuid4()
    store = AsyncMock(spec=GoogleCloudEventService)
    store.get_event_stream_ack.return_value = 2
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['X-Session-API-Key'] == 'sandbox-key'
        assert request.url.path == (
            f'/api/conversations/{conversation_id.hex}/events/webhook-replay'
        )
        params = dict(request.url.params)
        requests.append(params)
        sequence = int(params['after_seq']) + 1
        payload = {
            'kind': 'MessageEvent',
            'id': uuid4().hex,
            'timestamp': '2026-01-01T00:00:00Z',
            'source': 'user',
        }
        return httpx.Response(
            200,
            json={
                'through_seq': 4,
                'events': [
                    {'seq': sequence + int(gap and sequence == 4), 'event': payload}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        args = conversation_id, 'https://runtime.test', 'sandbox-key', client, store
        if gap:
            with pytest.raises(ValueError, match='Noncontiguous batch'):
                await final_events._reconcile_tail(*args)
        else:
            assert await final_events._reconcile_tail(*args) == 4
    assert requests == [{'after_seq': '2'}, {'after_seq': '3', 'through_seq': '4'}]
    saved_sequences = [
        [entry[0] for entry in call.args[1]]
        for call in store.save_event_stream_batch.await_args_list
    ]
    assert saved_sequences == ([[3]] if gap else [[3], [4]])


@pytest.mark.asyncio
async def test_reconciliation_writes_under_the_conversation_owner(monkeypatch):
    caller = SpecifyUserContext(user_id='caller')
    conversation = AppConversationInfo(
        id=uuid4(), sandbox_id='sandbox', created_by_user_id='owner'
    )
    info = AsyncMock()
    info.search_app_conversation_info.return_value = AppConversationInfoPage(
        items=[conversation]
    )
    store = AsyncMock(spec=GoogleCloudEventService)

    @asynccontextmanager
    async def get_info(state):
        assert getattr(state, USER_CONTEXT_ATTR) == caller
        yield info

    @asynccontextmanager
    async def get_db(_):
        yield

    @asynccontextmanager
    async def get_events(state):
        assert getattr(state, USER_CONTEXT_ATTR) == SandboxUserContext(
            user_id='owner', sandbox_id='sandbox'
        )
        yield store

    monkeypatch.setattr(config, 'get_app_conversation_info_service', get_info)
    monkeypatch.setattr(config, 'get_db_session', get_db)
    monkeypatch.setattr(config, 'get_event_service', get_events)
    reconcile = AsyncMock(return_value=7)
    monkeypatch.setattr(final_events, '_reconcile_tail', reconcile)

    async with httpx.AsyncClient() as client:
        await final_events.persist_sandbox_final_events(
            'sandbox',
            {'url': 'https://runtime.test', 'session_api_key': 'runtime-key'},
            caller,
            client,
        )
        reconcile.assert_awaited_once_with(
            conversation.id, 'https://runtime.test', 'runtime-key', client, store
        )
    assert info.search_app_conversation_info.call_args.kwargs['sandbox_id__eq'] == (
        'sandbox'
    )
