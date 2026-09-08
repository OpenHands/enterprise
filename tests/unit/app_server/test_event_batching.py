"""Tests for the lazy batch-file acceleration in EventServiceBase.

These exercise the batch lifecycle through the real ``FilesystemEventService``
(that reads/writes the local filesystem) so the batch orchestration logic in
``EventServiceBase`` is covered end-to-end:

- batches are created lazily on first search
- the first search after new events only re-reads the uncovered tail and
  overwrites/appends batches on the fixed grid (no deletes in steady state)
- pagination, filtering, count and export all read through batches
- stale batches (wrong size / off-grid) are rebuilt and deleted
- concurrent batch builds for one conversation are serialized
"""

import asyncio
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openhands.agent_server.models import EventPage
from openhands.app_server.event.event_service_base import BATCH_DIR_NAME
from openhands.app_server.event.filesystem_event_service import FilesystemEventService
from openhands.sdk.event import PauseEvent, TokenEvent


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def service(temp_dir: Path):
    """FilesystemEventService with a small batch size to exercise batching."""
    return FilesystemEventService(
        prefix=temp_dir,
        user_id='test_user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
        batch_size=3,
    )


def create_token_event() -> TokenEvent:
    return TokenEvent(
        source='agent', prompt_token_ids=[1, 2], response_token_ids=[3, 4]
    )


def create_pause_event() -> PauseEvent:
    return PauseEvent(source='user')


def _batch_files(conv_path: Path) -> list[Path]:
    bdir = conv_path / BATCH_DIR_NAME
    if not bdir.exists():
        return []
    return sorted(bdir.glob('batch_*.json'))


class TestBatchCreation:
    """Batches are created lazily on first search."""

    @pytest.mark.asyncio
    async def test_no_batches_before_first_search(self, service, temp_dir):
        conv_id = uuid4()
        for _ in range(5):
            await service.save_event(conv_id, create_token_event())

        conv_path = await service.get_conversation_path(conv_id)
        assert not (conv_path / BATCH_DIR_NAME).exists()

    @pytest.mark.asyncio
    async def test_search_creates_batches_on_fixed_grid(self, service):
        conv_id = uuid4()
        events = [create_token_event() for _ in range(7)]
        for e in events:
            await service.save_event(conv_id, e)

        await service.search_events(conv_id)

        conv_path = await service.get_conversation_path(conv_id)
        batches = _batch_files(conv_path)
        # batch_size=3, 7 events -> starts 0, 3, 6
        names = [b.name for b in batches]
        assert names == [
            'batch_00000000_3.json',
            'batch_00000003_3.json',
            'batch_00000006_3.json',
        ]

    @pytest.mark.asyncio
    async def test_individual_event_files_preserved(self, service):
        """Individual <id>.json files must remain for get_event by id."""
        conv_id = uuid4()
        events = [create_token_event() for _ in range(5)]
        for e in events:
            await service.save_event(conv_id, e)

        await service.search_events(conv_id)

        conv_path = await service.get_conversation_path(conv_id)
        individual = sorted(conv_path.glob('*.json'))
        # 5 individual files, none inside events_batch
        assert len(individual) == 5
        assert all(p.parent.name != BATCH_DIR_NAME for p in individual)


class TestSearchThroughBatches:
    """search_events correctness once batches exist."""

    @pytest.mark.asyncio
    async def test_search_returns_all_events_in_order(self, service):
        conv_id = uuid4()
        events = []
        for _ in range(8):
            e = create_token_event()
            events.append(e)
            await service.save_event(conv_id, e)
            time.sleep(0.01)

        result = await service.search_events(conv_id)
        assert isinstance(result, EventPage)
        assert len(result.items) == 8
        timestamps = [e.timestamp for e in result.items]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_search_filter_by_kind_with_batches(self, service):
        conv_id = uuid4()
        token_events = [create_token_event() for _ in range(4)]
        pause_events = [create_pause_event() for _ in range(2)]
        for e in token_events + pause_events:
            await service.save_event(conv_id, e)

        await service.search_events(conv_id)  # build batches
        result = await service.search_events(conv_id, kind__eq='TokenEvent')
        assert len(result.items) == 4
        for item in result.items:
            assert item.kind == 'TokenEvent'

    @pytest.mark.asyncio
    async def test_search_pagination_across_batches(self, service):
        """Pagination iterates all events exactly once via batches."""
        conv_id = uuid4()
        created_ids: set[str] = set()
        for _ in range(10):
            e = create_token_event()
            created_ids.add(e.id)
            await service.save_event(conv_id, e)

        await service.search_events(conv_id)  # build batches

        collected: set[str] = set()
        page_id = None
        while True:
            page = await service.search_events(conv_id, page_id=page_id, limit=3)
            for item in page.items:
                assert item.id not in collected
                collected.add(item.id)
            if page.next_page_id is None:
                break
            page_id = page.next_page_id

        assert collected == created_ids
        assert len(collected) == 10

    @pytest.mark.asyncio
    async def test_search_timestamp_filter(self, service):
        conv_id = uuid4()
        e1 = create_token_event()
        await service.save_event(conv_id, e1)
        time.sleep(0.01)
        cutoff = datetime.now()
        time.sleep(0.01)
        e2 = create_token_event()
        await service.save_event(conv_id, e2)

        await service.search_events(conv_id)
        result = await service.search_events(conv_id, timestamp__gte=cutoff)
        assert len(result.items) == 1
        assert result.items[0].id == e2.id


class TestBatchRegeneration:
    """Steady-state regeneration only touches the tail."""

    @pytest.mark.asyncio
    async def test_second_search_is_steady_state_no_writes(self, service):
        """A search after batches are up-to-date must not rewrite any batch."""
        conv_id = uuid4()
        for _ in range(6):
            await service.save_event(conv_id, create_token_event())

        await service.search_events(conv_id)
        conv_path = await service.get_conversation_path(conv_id)
        batches = {b.name: b.stat().st_mtime_ns for b in _batch_files(conv_path)}

        # Small delay so a rewrite would change mtime_ns
        time.sleep(0.01)
        await service.search_events(conv_id)

        after = {b.name: b.stat().st_mtime_ns for b in _batch_files(conv_path)}
        assert batches == after, 'steady-state search rewrote batch files'

    @pytest.mark.asyncio
    async def test_new_events_overwrite_last_partial_and_append(self, service):
        """Adding events overwrites the last (partial) batch and appends new ones."""
        conv_id = uuid4()
        for _ in range(4):  # batch_size=3 -> batches 0(3), 3(1 partial)
            await service.save_event(conv_id, create_token_event())
        await service.search_events(conv_id)

        conv_path = await service.get_conversation_path(conv_id)
        before = {b.name for b in _batch_files(conv_path)}
        assert before == {'batch_00000000_3.json', 'batch_00000003_3.json'}

        # Add 2 more -> 6 total -> batches 0(3), 3(3 full)
        for _ in range(2):
            await service.save_event(conv_id, create_token_event())
        await service.search_events(conv_id)

        after = {b.name for b in _batch_files(conv_path)}
        assert after == {'batch_00000000_3.json', 'batch_00000003_3.json'}
        result = await service.search_events(conv_id)
        assert len(result.items) == 6

    @pytest.mark.asyncio
    async def test_regen_only_reads_tail_not_full_history(self, service):
        """After batches exist, regeneration must not load already-batched events.

        We assert this by spying on _load_event: once batches cover events 0..5,
        adding event 6 and searching should load ONLY the new event file, not the
        six already-batched ones.
        """
        conv_id = uuid4()
        for _ in range(6):
            await service.save_event(conv_id, create_token_event())
        await service.search_events(conv_id)  # build batches

        # Add one new event
        new_event = create_token_event()
        await service.save_event(conv_id, new_event)

        load_calls: list[Path] = []
        original = service._load_event

        def spy(path: Path):
            load_calls.append(path)
            return original(path)

        service._load_event = spy  # type: ignore[assignment]
        try:
            await service.search_events(conv_id)
        finally:
            service._load_event = original  # type: ignore[assignment]

        # Only the single uncovered event file should have been read.
        assert len(load_calls) == 1, load_calls
        assert load_calls[0].stem == new_event.id.replace('-', '')


class TestStaleBatchHandling:
    """Batches written with a different size / off-grid are rebuilt and removed."""

    @pytest.mark.asyncio
    async def test_corrupt_batch_is_rebuilt(self, service, temp_dir):
        conv_id = uuid4()
        for _ in range(3):
            await service.save_event(conv_id, create_token_event())
        await service.search_events(conv_id)

        conv_path = await service.get_conversation_path(conv_id)
        batch_file = _batch_files(conv_path)[0]
        # Corrupt the batch contents
        batch_file.write_text('{not valid json')

        result = await service.search_events(conv_id)
        assert len(result.items) == 3
        # Batch should have been rewritten with valid content
        content = batch_file.read_text()
        assert '"events"' in content

    @pytest.mark.asyncio
    async def test_stale_size_batch_deleted_and_rebuilt(self, temp_dir):
        """A batch file with a non-current size is deleted and replaced."""
        # First service uses batch_size=3
        svc_a = FilesystemEventService(
            prefix=temp_dir,
            user_id='u',
            app_conversation_info_service=None,
            app_conversation_info_load_tasks={},
            batch_size=3,
        )
        conv_id = uuid4()
        for _ in range(5):
            await svc_a.save_event(conv_id, create_token_event())
        await svc_a.search_events(conv_id)
        conv_path = await svc_a.get_conversation_path(conv_id)
        assert {b.name for b in _batch_files(conv_path)} == {
            'batch_00000000_3.json',
            'batch_00000003_3.json',
        }

        # Now switch to batch_size=2 -> old batches are stale (size 3 != 2)
        svc_b = FilesystemEventService(
            prefix=temp_dir,
            user_id='u',
            app_conversation_info_service=None,
            app_conversation_info_load_tasks={},
            batch_size=2,
        )
        result = await svc_b.search_events(conv_id)
        assert len(result.items) == 5
        names = {b.name for b in _batch_files(conv_path)}
        # All batches now use size 2; old size-3 batches gone
        assert all(n.endswith('_2.json') for n in names)
        assert not any(n.endswith('_3.json') for n in names)


class TestCountAndExport:
    """count_events and iter_events_for_export route through batches."""

    @pytest.mark.asyncio
    async def test_count_no_filter_uses_batches(self, service):
        conv_id = uuid4()
        for _ in range(7):
            await service.save_event(conv_id, create_token_event())

        assert await service.count_events(conv_id) == 7
        # batches should now exist
        conv_path = await service.get_conversation_path(conv_id)
        assert len(_batch_files(conv_path)) == 3

    @pytest.mark.asyncio
    async def test_count_with_filter(self, service):
        conv_id = uuid4()
        for _ in range(4):
            await service.save_event(conv_id, create_token_event())
        for _ in range(2):
            await service.save_event(conv_id, create_pause_event())

        assert await service.count_events(conv_id, kind__eq='TokenEvent') == 4
        assert await service.count_events(conv_id, kind__eq='PauseEvent') == 2

    @pytest.mark.asyncio
    async def test_iter_events_for_export_order(self, service):
        conv_id = uuid4()
        events = []
        for _ in range(5):
            e = create_token_event()
            events.append(e)
            await service.save_event(conv_id, e)
            time.sleep(0.01)

        await service.search_events(conv_id)  # build batches
        exported = [e async for e in service.iter_events_for_export(conv_id)]
        assert [e.id for e in exported] == [e.id for e in events]
        timestamps = [e.timestamp for e in exported]
        assert timestamps == sorted(timestamps)


class TestConcurrency:
    """Per-conversation batch builds are serialized."""

    @pytest.mark.asyncio
    async def test_concurrent_searches_are_serialized(self, service):
        conv_id = uuid4()
        for _ in range(6):
            await service.save_event(conv_id, create_token_event())

        # Fire several searches concurrently; they share one lock
        results = await asyncio.gather(
            *(service.search_events(conv_id) for _ in range(5))
        )
        for r in results:
            assert len(r.items) == 6
        conv_path = await service.get_conversation_path(conv_id)
        assert len(_batch_files(conv_path)) == 2


class TestGetEventStillWorks:
    """get_event reads individual files regardless of batches."""

    @pytest.mark.asyncio
    async def test_get_event_after_batch_build(self, service):
        conv_id = uuid4()
        e = create_token_event()
        await service.save_event(conv_id, e)
        await service.search_events(conv_id)  # build batches

        # event.id is a str UUID; get_event expects a UUID object
        fetched = await service.get_event(conv_id, UUID(e.id))
        assert fetched is not None
        assert fetched.id == e.id
