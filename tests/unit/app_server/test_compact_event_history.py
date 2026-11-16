from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from openhands.agent_server.models import EventSortOrder
from openhands.app_server.event.event_router import search_events
from openhands.app_server.event.filesystem_event_service import FilesystemEventService
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, TextContent


@pytest.fixture
def service(tmp_path):
    return FilesystemEventService(
        prefix=tmp_path,
        user_id='test_user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
    )


def message(**kwargs):
    return MessageEvent(
        source='user',
        llm_message=Message(role='user', content=[TextContent(text='Hello')]),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_raw_history_preserves_full_snapshots_by_default(service):
    conversation_id = uuid4()
    snapshot = ConversationStateUpdateEvent(key='full_state', value={'agent': {}})
    msg = message()
    for event in [snapshot, msg]:
        await service.save_event(conversation_id, event)

    result = await search_events(conversation_id.hex, event_service=service)

    assert result.items == [snapshot, msg]
    assert result.next_page_id is None


@pytest.mark.asyncio
async def test_compact_history_preserves_other_state_and_messages(service):
    conversation_id = uuid4()
    snapshot = ConversationStateUpdateEvent(key='full_state', value={'agent': {}})
    retained = [
        message(),
        *[
            ConversationStateUpdateEvent(key=key, value={})
            for key in ['goal', 'execution_status', 'stats', 'future_state']
        ],
    ]
    for event in [snapshot, *retained]:
        await service.save_event(conversation_id, event)
    await service.search_events(conversation_id)
    raw_count = await service.count_events(conversation_id)

    result = await search_events(
        conversation_id.hex, exclude_full_state=True, event_service=service
    )

    assert result.items == retained
    assert result.next_page_id is None
    assert await service.count_events(conversation_id) == raw_count
    exported = [
        event async for event in service.iter_events_for_export(conversation_id)
    ]
    assert exported == [snapshot, *retained]


@pytest.mark.asyncio
async def test_compact_history_fills_pages_without_losing_cursor_or_filters(service):
    conversation_id = uuid4()
    first = message(timestamp='2026-01-01T00:00:02')
    second = message(timestamp='2026-01-01T00:00:04')
    third = message(timestamp='2026-01-01T00:00:05')
    events = [
        ConversationStateUpdateEvent(
            key='full_state', value={}, timestamp='2026-01-01T00:00:01'
        ),
        first,
        ConversationStateUpdateEvent(
            key='full_state', value={}, timestamp='2026-01-01T00:00:03'
        ),
        second,
        third,
        message(timestamp='2026-01-01T00:00:07'),
    ]
    for event in events:
        await service.save_event(conversation_id, event)
    cutoff = datetime(2026, 1, 1, 0, 0, 6)

    result = await search_events(
        conversation_id.hex,
        timestamp__lt=cutoff,
        limit=2,
        exclude_full_state=True,
        event_service=service,
    )

    assert result.items == [first, second]
    assert result.next_page_id == '4'
    following = await search_events(
        conversation_id.hex,
        timestamp__lt=cutoff,
        page_id=result.next_page_id,
        limit=2,
        exclude_full_state=True,
        event_service=service,
    )
    assert following.items == [third]
    assert following.next_page_id is None


@pytest.mark.asyncio
async def test_snapshot_only_history_finishes_with_an_empty_page(service):
    conversation_id = uuid4()
    await service.save_event(
        conversation_id, ConversationStateUpdateEvent(key='full_state', value={})
    )

    result = await search_events(
        conversation_id.hex, exclude_full_state=True, event_service=service
    )

    assert result.items == []
    assert result.next_page_id is None


@pytest.mark.asyncio
async def test_negative_cursor_fails_without_looping(service):
    conversation_id = uuid4()
    await service.save_event(conversation_id, message())
    with pytest.raises(ValueError, match='non-negative'):
        await search_events(
            conversation_id.hex,
            page_id='-100',
            exclude_full_state=True,
            event_service=service,
        )


@pytest.mark.asyncio
async def test_compact_history_propagates_storage_failure():
    service = AsyncMock()
    service.search_events.side_effect = RuntimeError('storage unavailable')

    with pytest.raises(RuntimeError, match='storage unavailable'):
        await search_events(uuid4().hex, exclude_full_state=True, event_service=service)


@pytest.mark.asyncio
async def test_new_events_do_not_shift_a_compact_page_while_it_is_loading(tmp_path):
    service = FilesystemEventService(
        prefix=tmp_path,
        user_id='test_user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
    )
    conversation_id = uuid4()
    older = message(timestamp='2026-01-01T00:00:01Z')
    newer = message(timestamp='2026-01-01T00:00:02Z')
    arriving = message(timestamp='2026-01-01T00:00:04Z')
    snapshot = ConversationStateUpdateEvent(
        key='full_state', value={}, timestamp='2026-01-01T00:00:03Z'
    )
    for event in [older, newer, snapshot]:
        await service.save_event(conversation_id, event)
    await service.search_events(conversation_id)

    original_load = service._load_events_from_paths
    appended = False

    async def load_with_arrival(self, paths):
        nonlocal appended
        loaded = await original_load(paths)
        if not appended:
            appended = True
            await service.save_event(conversation_id, arriving)
        return loaded

    with patch.object(
        FilesystemEventService, '_load_events_from_paths', load_with_arrival
    ):
        result = await search_events(
            conversation_id.hex,
            sort_order=EventSortOrder.TIMESTAMP_DESC,
            limit=2,
            exclude_full_state=True,
            event_service=service,
        )

    assert [event.id for event in result.items] == [newer.id, older.id]


@pytest.mark.asyncio
async def test_snapshot_runs_use_batched_reads_and_preserve_unconsumed_cursor(service):
    conversation_id = uuid4()
    events = [message(), message()]
    events.extend(
        ConversationStateUpdateEvent(key='full_state', value={}) for _ in range(7)
    )
    events.extend([message(), message()])
    for event in events:
        await service.save_event(conversation_id, event)
    await service.search_events(conversation_id)
    original_load = service._load_events_from_paths
    batch_sizes = []

    async def record_batch(self, paths):
        batch_sizes.append(len(paths))
        return await original_load(paths)

    with patch.object(FilesystemEventService, '_load_events_from_paths', record_batch):
        result = await service.search_events(
            conversation_id, limit=3, exclude_full_state=True
        )

    assert result.items == [events[0], events[1], events[9]]
    assert result.next_page_id == '10'
    assert len(batch_sizes) == 4
    following = await service.search_events(
        conversation_id, limit=3, page_id=result.next_page_id, exclude_full_state=True
    )
    assert following.items == [events[10]]
    assert following.next_page_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('excluded', ['snapshot', 'missing'])
async def test_compact_refill_limits_extra_storage_reads(
    service, monkeypatch, excluded
):
    monkeypatch.setenv('EVENT_SERVICE_LOAD_EVENT_CONCURRENCY', '4')
    conversation_id = uuid4()
    events = [message() for _ in range(40)]
    if excluded == 'snapshot':
        events[18:20] = [
            ConversationStateUpdateEvent(key='full_state', value={}) for _ in range(2)
        ]
    events = [
        event.model_copy(
            update={
                'timestamp': (datetime(2026, 1, 1) + timedelta(seconds=i)).isoformat()
            }
        )
        for i, event in enumerate(events)
    ]
    for event in events:
        await service.save_event(conversation_id, event)
    await service.search_events(conversation_id)
    if excluded == 'missing':
        directory = await service.get_conversation_path(conversation_id)
        for event in events[18:20]:
            service._event_id_to_path(directory, event.id.replace('-', '')).unlink()

    with patch.object(service, '_load_event', wraps=service._load_event) as load:
        page = await service.search_events(
            conversation_id, limit=20, exclude_full_state=True
        )

    expected = events[:18] + events[20:]
    assert page.items == expected[:20]
    assert page.next_page_id == '22'
    assert load.call_count <= 24
    following = await service.search_events(
        conversation_id, limit=20, page_id=page.next_page_id, exclude_full_state=True
    )
    assert following.items == expected[20:]
    assert following.next_page_id is None


@pytest.mark.asyncio
async def test_compact_refill_keeps_snapshot_runs_batched(service, monkeypatch):
    monkeypatch.setenv('EVENT_SERVICE_LOAD_EVENT_CONCURRENCY', '4')
    conversation_id = uuid4()
    messages = [message() for _ in range(20)]
    events = [
        *messages[:19],
        *[ConversationStateUpdateEvent(key='full_state', value={}) for _ in range(21)],
        messages[-1],
    ]
    events = [
        event.model_copy(
            update={
                'timestamp': (datetime(2026, 1, 1) + timedelta(seconds=i)).isoformat()
            }
        )
        for i, event in enumerate(events)
    ]
    for event in events:
        await service.save_event(conversation_id, event)
    await service.search_events(conversation_id)

    with patch.object(
        service, '_load_events_from_paths', wraps=service._load_events_from_paths
    ) as load:
        page = await service.search_events(
            conversation_id, limit=20, exclude_full_state=True
        )

    assert page.items == events[:19] + events[-1:]
    assert page.next_page_id is None
    assert load.call_count <= 7
