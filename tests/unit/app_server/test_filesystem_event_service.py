"""Tests for FilesystemEventService.

This module tests the filesystem-based implementation of EventService,
focusing on search functionality.
"""

import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from openhands.agent_server.models import EventPage, EventSortOrder
from openhands.app_server.event.filesystem_event_service import FilesystemEventService
from openhands.sdk import Event
from openhands.sdk.event import PauseEvent, TokenEvent


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def service(temp_dir: Path) -> FilesystemEventService:
    """Create a FilesystemEventService instance for testing."""
    return FilesystemEventService(
        prefix=temp_dir,
        user_id='test_user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
    )


@pytest.fixture
def service_no_user(temp_dir: Path) -> FilesystemEventService:
    """Create a FilesystemEventService instance without user_id."""
    return FilesystemEventService(
        prefix=temp_dir,
        user_id=None,
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
    )


def create_token_event() -> TokenEvent:
    """Helper to create a TokenEvent for testing."""
    return TokenEvent(
        source='agent', prompt_token_ids=[1, 2], response_token_ids=[3, 4]
    )


def create_pause_event() -> PauseEvent:
    """Helper to create a PauseEvent for testing."""
    return PauseEvent(source='user')


class TestFilesystemEventServiceSearchEvents:
    """Test cases for search_events method."""

    @pytest.mark.asyncio
    async def test_search_events_returns_all_events(
        self, service: FilesystemEventService
    ):
        """Test that search_events returns all events when no filters are applied."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]

        for event in events:
            await service.save_event(conversation_id, event)

        result = await service.search_events(conversation_id)

        assert isinstance(result, EventPage)
        assert len(result.items) == 3
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_empty_conversation(
        self, service: FilesystemEventService
    ):
        """Test that search_events returns empty page for a conversation with no events."""
        conversation_id = uuid4()

        result = await service.search_events(conversation_id)

        assert isinstance(result, EventPage)
        assert len(result.items) == 0
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_filter_by_kind(self, service: FilesystemEventService):
        """Test that search_events filters events by kind."""
        conversation_id = uuid4()
        token_events = [create_token_event() for _ in range(2)]
        pause_event = create_pause_event()

        for event in token_events:
            await service.save_event(conversation_id, event)
        await service.save_event(conversation_id, pause_event)

        result = await service.search_events(conversation_id, kind__eq='TokenEvent')

        assert len(result.items) == 2
        for item in result.items:
            assert item.kind == 'TokenEvent'

    @pytest.mark.asyncio
    async def test_search_events_sort_ascending(self, service: FilesystemEventService):
        """Test that search_events sorts events by timestamp ascending."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]

        for event in events:
            await service.save_event(conversation_id, event)

        result = await service.search_events(
            conversation_id, sort_order=EventSortOrder.TIMESTAMP
        )

        assert len(result.items) == 3
        # Verify items are sorted by timestamp ascending
        timestamps = [item.timestamp for item in result.items]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_search_events_sort_descending(self, service: FilesystemEventService):
        """Test that search_events sorts events by timestamp descending."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]

        for event in events:
            await service.save_event(conversation_id, event)

        result = await service.search_events(
            conversation_id, sort_order=EventSortOrder.TIMESTAMP_DESC
        )

        assert len(result.items) == 3
        # Verify items are sorted by timestamp descending
        timestamps = [item.timestamp for item in result.items]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.asyncio
    async def test_iter_events_for_export_returns_all_events_in_timestamp_order(
        self, service: FilesystemEventService
    ):
        """Test export iterator returns all events once in timestamp order."""
        conversation_id = uuid4()
        events = []

        for _ in range(3):
            event = create_token_event()
            events.append(event)
            await service.save_event(conversation_id, event)
            time.sleep(0.01)

        result = [
            event async for event in service.iter_events_for_export(conversation_id)
        ]

        assert [event.id for event in result] == [event.id for event in events]
        assert [event.timestamp for event in result] == sorted(
            event.timestamp for event in result
        )

    @pytest.mark.asyncio
    async def test_search_events_returns_event_page(
        self, service: FilesystemEventService
    ):
        """Test that search_events returns an EventPage with correct structure."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]

        for event in events:
            await service.save_event(conversation_id, event)

        result = await service.search_events(conversation_id)

        # Verify the EventPage structure
        assert isinstance(result, EventPage)
        assert hasattr(result, 'items')
        assert hasattr(result, 'next_page_id')
        assert len(result.items) == 3

    @pytest.mark.asyncio
    async def test_search_events_pagination_limits_results(
        self, service: FilesystemEventService
    ):
        """Test that search_events respects the limit parameter for pagination."""
        conversation_id = uuid4()
        total_events = 10
        page_limit = 3

        # Create more events than the limit
        for _ in range(total_events):
            await service.save_event(conversation_id, create_token_event())

        # First page should return only 'limit' events
        result = await service.search_events(conversation_id, limit=page_limit)

        assert len(result.items) == page_limit
        assert result.next_page_id is not None

    @pytest.mark.asyncio
    async def test_search_events_pagination_iterates_all_events(
        self, service: FilesystemEventService
    ):
        """Test that pagination correctly iterates through all events without duplicates.

        This test verifies the fix for a bug where pagination was applied to 'paths'
        instead of 'items', causing all events to be returned on every page.
        """
        conversation_id = uuid4()
        total_events = 10
        page_limit = 3

        # Create events and track their IDs
        created_event_ids = set()
        for _ in range(total_events):
            event = create_token_event()
            created_event_ids.add(event.id)
            await service.save_event(conversation_id, event)

        # Iterate through all pages and collect event IDs
        collected_event_ids = set()
        page_id = None
        page_count = 0

        while True:
            result = await service.search_events(
                conversation_id, page_id=page_id, limit=page_limit
            )
            page_count += 1

            for item in result.items:
                # Verify no duplicates - this would fail with the old buggy code
                assert item.id not in collected_event_ids, (
                    f'Duplicate event {item.id} found on page {page_count}'
                )
                collected_event_ids.add(item.id)

            if result.next_page_id is None:
                break
            page_id = result.next_page_id

        # Verify we got all events exactly once
        assert collected_event_ids == created_event_ids
        assert len(collected_event_ids) == total_events

        # With 10 events and limit of 3, we should have 4 pages (3+3+3+1)
        expected_pages = (total_events + page_limit - 1) // page_limit
        assert page_count == expected_pages

    @pytest.mark.asyncio
    async def test_search_events_pagination_with_filters(
        self, service: FilesystemEventService
    ):
        """Test that pagination works correctly when combined with filters."""
        conversation_id = uuid4()

        # Create a mix of events
        token_events = [create_token_event() for _ in range(5)]
        pause_events = [create_pause_event() for _ in range(3)]

        for event in token_events + pause_events:
            await service.save_event(conversation_id, event)

        # Search only for token events with pagination
        page_limit = 2
        collected_ids = set()
        page_id = None

        while True:
            result = await service.search_events(
                conversation_id,
                kind__eq='TokenEvent',
                page_id=page_id,
                limit=page_limit,
            )

            for item in result.items:
                assert item.kind == 'TokenEvent'
                collected_ids.add(item.id)

            if result.next_page_id is None:
                break
            page_id = result.next_page_id

        # Should have found all 5 token events
        assert len(collected_ids) == 5

    @pytest.mark.asyncio
    async def test_search_events_filter_by_timestamp_gte(
        self, service: FilesystemEventService
    ):
        """Test that search_events filters events by timestamp__gte.

        This verifies the fix for a bug where event.timestamp (str) was
        compared directly against a datetime object, raising TypeError.
        """
        conversation_id = uuid4()

        # Create events with a small delay so timestamps differ
        early_event = create_token_event()
        await service.save_event(conversation_id, early_event)
        time.sleep(0.01)

        cutoff = datetime.now()
        time.sleep(0.01)

        late_event = create_token_event()
        await service.save_event(conversation_id, late_event)

        result = await service.search_events(conversation_id, timestamp__gte=cutoff)

        assert len(result.items) == 1
        assert result.items[0].id == late_event.id

    @pytest.mark.asyncio
    async def test_search_events_filter_by_timestamp_lt(
        self, service: FilesystemEventService
    ):
        """Test that search_events filters events by timestamp__lt.

        This verifies the fix for a bug where event.timestamp (str) was
        compared directly against a datetime object, raising TypeError.
        """
        conversation_id = uuid4()

        early_event = create_token_event()
        await service.save_event(conversation_id, early_event)
        time.sleep(0.01)

        cutoff = datetime.now()
        time.sleep(0.01)

        late_event = create_token_event()
        await service.save_event(conversation_id, late_event)

        result = await service.search_events(conversation_id, timestamp__lt=cutoff)

        assert len(result.items) == 1
        assert result.items[0].id == early_event.id

    @pytest.mark.asyncio
    async def test_search_events_filter_by_timestamp_range(
        self, service: FilesystemEventService
    ):
        """Test that search_events filters events by a timestamp range."""
        conversation_id = uuid4()

        event1 = create_token_event()
        await service.save_event(conversation_id, event1)
        time.sleep(0.01)

        range_start = datetime.now()
        time.sleep(0.01)

        event2 = create_token_event()
        await service.save_event(conversation_id, event2)
        time.sleep(0.01)

        range_end = datetime.now()
        time.sleep(0.01)

        event3 = create_token_event()
        await service.save_event(conversation_id, event3)

        result = await service.search_events(
            conversation_id,
            timestamp__gte=range_start,
            timestamp__lt=range_end,
        )

        assert len(result.items) == 1
        assert result.items[0].id == event2.id

    @pytest.mark.asyncio
    async def test_search_events_timestamp_filter_with_desc_sort(
        self, service: FilesystemEventService
    ):
        """Test timestamp filters combined with TIMESTAMP_DESC sort order."""
        conversation_id = uuid4()

        event1 = create_token_event()
        await service.save_event(conversation_id, event1)
        time.sleep(0.01)

        cutoff = datetime.now()
        time.sleep(0.01)

        event2 = create_token_event()
        await service.save_event(conversation_id, event2)
        time.sleep(0.01)

        event3 = create_token_event()
        await service.save_event(conversation_id, event3)

        result = await service.search_events(
            conversation_id,
            timestamp__gte=cutoff,
            sort_order=EventSortOrder.TIMESTAMP_DESC,
        )

        assert len(result.items) == 2
        # Descending: event3 before event2
        assert result.items[0].id == event3.id
        assert result.items[1].id == event2.id


class TestFilesystemEventServiceIntegration:
    """Integration tests for FilesystemEventService."""

    @pytest.mark.asyncio
    async def test_get_conversation_path_with_user_id(
        self, service: FilesystemEventService, temp_dir: Path
    ):
        """Test conversation path generation with user_id."""
        conversation_id = uuid4()

        path = await service.get_conversation_path(conversation_id)

        assert str(temp_dir) in str(path)
        assert 'test_user' in str(path)
        assert 'v1_conversations' in str(path)
        assert conversation_id.hex in str(path)

    @pytest.mark.asyncio
    async def test_get_conversation_path_without_user_id(
        self, service_no_user: FilesystemEventService, temp_dir: Path
    ):
        """Test conversation path generation without user_id."""
        conversation_id = uuid4()

        path = await service_no_user.get_conversation_path(conversation_id)

        assert str(temp_dir) in str(path)
        assert 'test_user' not in str(path)
        assert 'v1_conversations' in str(path)
        assert conversation_id.hex in str(path)

    @pytest.mark.asyncio
    async def test_save_and_get_event(self, service: FilesystemEventService):
        """Test saving and retrieving an event."""
        conversation_id = uuid4()
        event = create_token_event()

        await service.save_event(conversation_id, event)

        conversation_path = await service.get_conversation_path(conversation_id)
        event_id_hex = event.id.replace('-', '')
        event_file = conversation_path / f'{event_id_hex}.json'
        assert event_file.exists()

    @pytest.mark.asyncio
    async def test_save_multiple_events(self, service: FilesystemEventService):
        """Test saving multiple events to the same conversation."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]

        for event in events:
            await service.save_event(conversation_id, event)

        result = await service.search_events(conversation_id)
        assert len(result.items) == 3


class TestEventIndex:
    """Tests for the per-conversation event index.

    The index is a JSON file of [[event_id, timestamp, kind], ...] stored next
    to the events. search_events/count_events use it to avoid loading every
    event; save_event invalidates it.
    """

    @pytest.mark.asyncio
    async def test_search_creates_index_file(self, service: FilesystemEventService):
        """A search on a conversation with no index rebuilds and writes index.json."""
        conversation_id = uuid4()
        for _ in range(3):
            await service.save_event(conversation_id, create_token_event())

        # save_event invalidates, so index.json should be absent, index_stale absent
        # (first time, there was nothing to rename).
        conversation_path = await service.get_conversation_path(conversation_id)
        index_path = service._index_path(conversation_path)
        stale_path = service._index_stale_path(conversation_path)
        assert not index_path.exists()
        assert not stale_path.exists()

        # A search rebuilds the index.
        await service.search_events(conversation_id)
        assert index_path.exists()

    @pytest.mark.asyncio
    async def test_search_uses_existing_index_no_scan(
        self, service: FilesystemEventService
    ):
        """When index.json is present and fresh, search reads it without rebuilding."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]
        for event in events:
            await service.save_event(conversation_id, event)

        # First search builds the index.
        await service.search_events(conversation_id)
        await service.get_conversation_path(conversation_id)

        # Patch _search_paths to raise if called -- the fresh-index path must
        # never scan.
        original_search_paths = service._search_paths

        def fail_if_scanned(prefix: Path, page_id: str | None = None) -> list[Path]:
            raise AssertionError('search_events scanned paths on fresh index')

        service._search_paths = fail_if_scanned  # type: ignore[assignment]
        try:
            result = await service.search_events(conversation_id)
            assert len(result.items) == 3
        finally:
            service._search_paths = original_search_paths  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_save_event_invalidates_index(self, service: FilesystemEventService):
        """save_event renames index.json -> index_stale.json (idempotent)."""
        conversation_id = uuid4()
        await service.save_event(conversation_id, create_token_event())
        await service.search_events(conversation_id)  # builds index.json
        conversation_path = await service.get_conversation_path(conversation_id)
        index_path = service._index_path(conversation_path)
        stale_path = service._index_stale_path(conversation_path)
        assert index_path.exists()
        assert not stale_path.exists()

        await service.save_event(conversation_id, create_token_event())
        assert not index_path.exists()
        assert stale_path.exists()

        # Second save with no index.json: idempotent no-op, stale stays.
        await service.save_event(conversation_id, create_token_event())
        assert not index_path.exists()
        assert stale_path.exists()

    @pytest.mark.asyncio
    async def test_search_rebuilds_from_stale_incrementally(
        self, service: FilesystemEventService
    ):
        """After invalidation, search seeds from index_stale.json and scans the diff."""
        conversation_id = uuid4()
        # Save 2 events, build index.
        e1 = create_token_event()
        e2 = create_token_event()
        await service.save_event(conversation_id, e1)
        await service.save_event(conversation_id, e2)
        await service.search_events(conversation_id)

        # Save a 3rd event -> invalidates.
        e3 = create_token_event()
        await service.save_event(conversation_id, e3)

        conversation_path = await service.get_conversation_path(conversation_id)
        stale_path = service._index_stale_path(conversation_path)
        assert stale_path.exists()

        # Search should rebuild: seed from stale (2 entries) + scan diff (1 event).
        result = await service.search_events(conversation_id)
        ids = {e1.id, e2.id, e3.id}
        assert {item.id for item in result.items} == ids
        # Fresh index now exists with 3 entries.
        import json

        index_path = service._index_path(conversation_path)
        data = json.loads(index_path.read_text())
        assert len(data) == 3

    @pytest.mark.asyncio
    async def test_search_full_rebuild_when_no_index(
        self, service: FilesystemEventService
    ):
        """With neither index.json nor index_stale.json, search does a full scan."""
        conversation_id = uuid4()
        events = [create_token_event() for _ in range(3)]
        for event in events:
            await service.save_event(conversation_id, event)

        # No search yet -> no index files at all.
        result = await service.search_events(conversation_id)
        assert len(result.items) == 3

    @pytest.mark.asyncio
    async def test_malformed_index_triggers_rebuild(
        self, service: FilesystemEventService
    ):
        """A corrupt index.json is treated as missing and rebuilt."""
        conversation_id = uuid4()
        await service.save_event(conversation_id, create_token_event())
        await service.search_events(conversation_id)  # build valid index

        conversation_path = await service.get_conversation_path(conversation_id)
        index_path = service._index_path(conversation_path)
        # Corrupt the index.
        index_path.write_text('{not valid json')

        result = await service.search_events(conversation_id)
        assert len(result.items) == 1
        # Index was rewritten correctly.
        import json

        data = json.loads(index_path.read_text())
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_malformed_stale_index_triggers_full_rebuild(
        self, service: FilesystemEventService
    ):
        """A corrupt index_stale.json is ignored, falling back to a full scan."""
        conversation_id = uuid4()
        await service.save_event(conversation_id, create_token_event())
        await service.search_events(conversation_id)
        conversation_path = await service.get_conversation_path(conversation_id)
        index_path = service._index_path(conversation_path)
        stale_path = service._index_stale_path(conversation_path)

        # Simulate: index.json gone (stale), stale corrupt.
        index_path.unlink()
        stale_path.write_text('not json')

        result = await service.search_events(conversation_id)
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_filtered_count_uses_index_no_event_loads(
        self, service: FilesystemEventService
    ):
        """Filtered count_events counts index entries without loading events."""
        conversation_id = uuid4()
        token_events = [create_token_event() for _ in range(3)]
        pause_event = create_pause_event()
        for event in token_events + [pause_event]:
            await service.save_event(conversation_id, event)

        # Build the index.
        await service.search_events(conversation_id)

        # Patch _load_event to fail if called.
        original_load = service._load_event

        def fail_if_loaded(path: Path) -> Event | None:
            raise AssertionError('count_events loaded an event from the index path')

        service._load_event = fail_if_loaded  # type: ignore[assignment]
        try:
            count = await service.count_events(conversation_id, kind__eq='TokenEvent')
            assert count == 3
        finally:
            service._load_event = original_load  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_count_events_no_filter_counts_paths(
        self, service: FilesystemEventService
    ):
        """Unfiltered count still uses the cheap path-count, not the index."""
        conversation_id = uuid4()
        for _ in range(4):
            await service.save_event(conversation_id, create_token_event())

        count = await service.count_events(conversation_id)
        assert count == 4

    @pytest.mark.asyncio
    async def test_index_dedups_by_event_id(self, service: FilesystemEventService):
        """Re-saving the same event id does not duplicate it in the index."""
        conversation_id = uuid4()
        event = create_token_event()
        await service.save_event(conversation_id, event)
        # Re-save the same id (simulating an idempotent retry).
        await service.save_event(conversation_id, event)

        result = await service.search_events(conversation_id)
        ids = [item.id for item in result.items]
        assert len(ids) == 1
        assert ids[0] == event.id

    @pytest.mark.asyncio
    async def test_index_handles_out_of_order_timestamps(
        self, service: FilesystemEventService
    ):
        """Events saved out of timestamp order are sorted correctly via the index."""
        conversation_id = uuid4()
        # Create events with explicit timestamps in reverse order.
        from datetime import datetime, timedelta

        base = datetime(2025, 1, 1, 12, 0, 0)
        events = []
        for i in range(3):
            e = create_token_event()
            # Set timestamps: newest first in save order.
            e = e.model_copy(
                update={'timestamp': (base + timedelta(hours=2 - i)).isoformat()}
            )
            events.append(e)
            await service.save_event(conversation_id, e)

        # Ascending sort.
        result = await service.search_events(
            conversation_id, sort_order=EventSortOrder.TIMESTAMP
        )
        timestamps = [item.timestamp for item in result.items]
        assert timestamps == sorted(timestamps)

        # Descending sort.
        result_desc = await service.search_events(
            conversation_id, sort_order=EventSortOrder.TIMESTAMP_DESC
        )
        timestamps_desc = [item.timestamp for item in result_desc.items]
        assert timestamps_desc == sorted(timestamps_desc, reverse=True)

    @pytest.mark.asyncio
    async def test_pagination_only_loads_page_events(
        self, service: FilesystemEventService
    ):
        """Pagination loads only the events for the requested page."""
        conversation_id = uuid4()
        for _ in range(10):
            await service.save_event(conversation_id, create_token_event())

        # Build index.
        await service.search_events(conversation_id, limit=100)

        # Track which events get loaded on a single page request.
        loaded_paths: list[Path] = []
        original_load = service._load_event

        def tracking_load(path: Path) -> Event | None:
            loaded_paths.append(path)
            return original_load(path)

        service._load_event = tracking_load  # type: ignore[assignment]
        try:
            result = await service.search_events(conversation_id, limit=3)
            assert len(result.items) == 3
            # Only the 3 page events should be loaded, not all 10.
            assert len(loaded_paths) == 3
        finally:
            service._load_event = original_load  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_iter_events_for_export_uses_index(
        self, service: FilesystemEventService
    ):
        """iter_events_for_export yields all events in timestamp order via the index."""
        import time

        conversation_id = uuid4()
        events = []
        for _ in range(3):
            event = create_token_event()
            events.append(event)
            await service.save_event(conversation_id, event)
            time.sleep(0.01)

        result = [
            event async for event in service.iter_events_for_export(conversation_id)
        ]
        assert [event.id for event in result] == [event.id for event in events]
        assert [event.timestamp for event in result] == sorted(
            event.timestamp for event in result
        )
