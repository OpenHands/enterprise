import asyncio
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator
from uuid import UUID

from openhands.agent_server.models import EventPage, EventSortOrder
from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.conversation_paths import V1_CONVERSATIONS_DIR
from openhands.app_server.event.event_service import EventService
from openhands.app_server.event_callback.event_callback_models import EventKind
from openhands.sdk import Event
from openhands.sdk.utils.paging import page_iterator

_logger = logging.getLogger(__name__)

# Directory (adjacent to the event files) holding batch files.
BATCH_DIR_NAME = 'events_batch'
# Batch files are named ``batch_{start:08d}_{size}.json`` on a fixed grid whose
# boundaries are multiples of ``size``.  Embedding ``size`` in the name lets us
# cheaply detect stale batches after a config change without reading them.
_BATCH_RE = re.compile(r'^batch_(\d+)_(\d+)\.json$')


def _event_load_concurrency() -> int:
    try:
        return max(1, int(os.getenv('EVENT_SERVICE_LOAD_EVENT_CONCURRENCY', '10')))
    except ValueError:
        return 10


def _event_id_from_event(event: Event) -> str:
    """Return the no-dash hex id used for individual event file names."""
    if isinstance(event.id, str):
        return event.id.replace('-', '')
    return event.id.hex  # type: ignore[unreachable]


def _serialize_batch(events: list[Event]) -> str:
    """Serialize a sorted-by-timestamp batch of events to JSON.

    The wrapper records ``start``/``count``/``min_timestamp``/``max_timestamp``
    alongside the events.  ``min``/``max`` let a future caller skip whole batches
    for timestamp-range filters without parsing the events array.
    """
    sorted_events = sorted(events, key=lambda e: e.timestamp)
    data: dict[str, object] = {
        'count': len(sorted_events),
        'events': [e.model_dump(mode='json') for e in sorted_events],
    }
    if sorted_events:
        data['min_timestamp'] = sorted_events[0].timestamp
        data['max_timestamp'] = sorted_events[-1].timestamp
    return json.dumps(data, indent=2)


def _parse_batch(content: str) -> list[Event] | None:
    """Parse a batch file's JSON content into a list of events.

    Returns ``None`` if the file is missing or corrupt so the caller can treat
    the batch as uncovered and rebuild it.
    """
    try:
        data = json.loads(content)
        raw_events = data.get('events', []) if isinstance(data, dict) else []
        return [Event.model_validate(item) for item in raw_events]
    except Exception:
        return None


@dataclass
class EventServiceBase(EventService, ABC):
    """Event Service for getting events - the only check on permissions for events is
    in the strict prefix for storage.
    """

    prefix: Path
    user_id: str | None
    app_conversation_info_service: AppConversationInfoService | None
    app_conversation_info_load_tasks: dict[
        UUID, asyncio.Task[AppConversationInfo | None]
    ]
    # Number of events per batch file. ``kw_only`` so adding defaulted fields to the
    # base does not break the non-defaulted fields (``s3_client``, ``bucket``, ...)
    # declared by concrete subclasses.
    batch_size: int = field(default=25, kw_only=True)
    _batch_locks: dict[str, asyncio.Lock] = field(default_factory=dict, kw_only=True)

    @abstractmethod
    def _load_event(self, path: Path) -> Event | None:
        """Get the event at the path given."""

    @abstractmethod
    def _store_event(self, path: Path, event: Event):
        """Store the event given at the path given."""

    @abstractmethod
    def _search_paths(self, prefix: Path) -> list[Path]:
        """Search paths."""

    @abstractmethod
    def _load_batch(self, path: Path) -> list[Event] | None:
        """Load the events contained in a batch file.

        Returns ``None`` if the file is missing or corrupt.
        """

    @abstractmethod
    def _store_batch(self, path: Path, events: list[Event]):
        """Store a batch of events at the given path."""

    @abstractmethod
    def _delete_path(self, path: Path):
        """Delete the object/file at the given path (no-op if missing)."""

    async def _load_events_from_paths(self, paths: list[Path]) -> list[Event | None]:
        loop = asyncio.get_running_loop()
        semaphore = asyncio.Semaphore(_event_load_concurrency())

        async def load_event(path: Path) -> Event | None:
            async with semaphore:
                return await loop.run_in_executor(None, self._load_event, path)

        return await asyncio.gather(*(load_event(path) for path in paths))

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def _batch_dir(self, prefix: Path) -> Path:
        return prefix / BATCH_DIR_NAME

    def _batch_path(self, prefix: Path, start: int, size: int) -> Path:
        return self._batch_dir(prefix) / f'batch_{start:08d}_{size}.json'

    @staticmethod
    def _categorize_paths(
        paths: list[Path],
    ) -> tuple[list[Path], list[Path]]:
        """Split paths into (batch_paths, event_paths).

        Batch files live under ``events_batch/`` and match ``batch_*_*.json``;
        individual event files are any other ``*.json``.
        """
        batch_paths: list[Path] = []
        event_paths: list[Path] = []
        for p in paths:
            if p.parent.name == BATCH_DIR_NAME and _BATCH_RE.match(p.name):
                batch_paths.append(p)
            elif p.name.endswith('.json'):
                event_paths.append(p)
        return batch_paths, event_paths

    async def _get_batch_lock(self, key: str) -> asyncio.Lock:
        lock = self._batch_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._batch_locks[key] = lock
        return lock

    async def _load_batches_from_paths(
        self, paths: list[Path]
    ) -> list[tuple[Path, int, int, list[Event] | None]]:
        """Load batch files, returning (path, start, size, events|None) tuples."""
        loop = asyncio.get_running_loop()
        semaphore = asyncio.Semaphore(_event_load_concurrency())

        async def load_one(
            path: Path,
        ) -> tuple[Path, int, int, list[Event] | None]:
            m = _BATCH_RE.match(path.name)
            if not m:
                return (path, 0, 0, None)
            start = int(m.group(1))
            size = int(m.group(2))
            async with semaphore:
                events = await loop.run_in_executor(None, self._load_batch, path)
            return (path, start, size, events)

        return await asyncio.gather(*(load_one(p) for p in paths))

    async def _load_all_events(
        self, prefix: Path, regenerate: bool = True
    ) -> list[Event]:
        """Load all events for a conversation, using batch files when present.

        Batch files are created/refreshed lazily on a fixed grid (multiples of
        ``batch_size``).  Individual ``<event_id>.json`` files are always kept, so
        ``get_event`` by id still works; batches are only an *additional* read
        acceleration for search/export/count.

        Returns events sorted ascending by timestamp.
        """
        lock = await self._get_batch_lock(str(prefix))
        async with lock:
            loop = asyncio.get_running_loop()
            paths = await loop.run_in_executor(None, self._search_paths, prefix)
            batch_paths, event_paths = self._categorize_paths(paths)

            loaded = await self._load_batches_from_paths(batch_paths)

            valid_batches: dict[int, list[Event]] = {}
            stale_batch_paths: list[Path] = []
            covered_ids: set[str] = set()
            for path, start, size, events in loaded:
                # A batch is current only if it sits on the active grid and was
                # written with the configured batch size.
                if (
                    size != self.batch_size
                    or start % self.batch_size != 0
                    or events is None
                ):
                    stale_batch_paths.append(path)
                    continue
                valid_batches[start] = events
                for e in events:
                    covered_ids.add(_event_id_from_event(e))

            # Individual files not yet folded into a batch.
            uncovered_paths = [p for p in event_paths if p.stem not in covered_ids]
            uncovered_results = await self._load_events_from_paths(uncovered_paths)
            uncovered_events = [e for e in uncovered_results if e is not None]

            # Merge: batches are pre-sorted ascending within their grid slot, but
            # we re-sort the full set to be safe against ordering assumptions.
            all_events: list[Event] = []
            for start in sorted(valid_batches):
                all_events.extend(valid_batches[start])
            all_events.extend(uncovered_events)
            all_events.sort(key=lambda e: e.timestamp)

            if regenerate:
                await self._regenerate_batches(
                    prefix, all_events, valid_batches, stale_batch_paths
                )

            return all_events

    async def _regenerate_batches(
        self,
        prefix: Path,
        all_events: list[Event],
        valid_batches: dict[int, list[Event]],
        stale_batch_paths: list[Path],
    ) -> None:
        """Bring batch files in sync with ``all_events`` on the current grid.

        Only writes batches that are missing or whose count differs from the
        desired count (steady state = zero writes).  Stale batches (wrong size /
        off-grid) and batches beyond the event range are deleted.
        """
        loop = asyncio.get_running_loop()
        size = self.batch_size
        n = len(all_events)
        desired_starts: set[int] = set(range(0, n, size)) if n else set()

        to_write: dict[int, list[Event]] = {}
        for start in desired_starts:
            desired_count = min(size, n - start)
            existing = valid_batches.get(start)
            if existing is not None and len(existing) == desired_count:
                continue  # already correct - skip the write
            to_write[start] = all_events[start : start + size]

        # Paths we are about to (over)write must not also be deleted, otherwise a
        # stale/corrupt batch sitting on a current grid slot would be removed right
        # after being rewritten.
        write_paths = {self._batch_path(prefix, start, size) for start in to_write}
        to_delete: list[Path] = [p for p in stale_batch_paths if p not in write_paths]
        for start in valid_batches:
            if start not in desired_starts:
                p = self._batch_path(prefix, start, size)
                if p not in write_paths:
                    to_delete.append(p)

        if not to_write and not to_delete:
            return

        for start, events in to_write.items():
            path = self._batch_path(prefix, start, size)
            await loop.run_in_executor(None, self._store_batch, path, events)
        for path in to_delete:
            await loop.run_in_executor(None, self._delete_path, path)

    async def get_conversation_path(self, conversation_id: UUID) -> Path:
        """Get a path for a conversation. Ensure user_id is included if possible."""
        path = self.prefix
        if self.user_id:
            path /= self.user_id
        elif self.app_conversation_info_service:
            task = self.app_conversation_info_load_tasks.get(conversation_id)
            if task is None:
                task = asyncio.create_task(
                    self.app_conversation_info_service.get_app_conversation_info(
                        conversation_id
                    )
                )
                self.app_conversation_info_load_tasks[conversation_id] = task
            conversation_info = await task
            if conversation_info and conversation_info.created_by_user_id:
                path /= conversation_info.created_by_user_id
        path = path / V1_CONVERSATIONS_DIR / conversation_id.hex
        return path

    async def get_event(self, conversation_id: UUID, event_id: UUID) -> Event | None:
        """Get the event with the given id, or None if not found."""
        conversation_path = await self.get_conversation_path(conversation_id)
        path = conversation_path / f'{event_id.hex}.json'
        loop = asyncio.get_running_loop()
        event: Event = await loop.run_in_executor(None, self._load_event, path)  # type: ignore[arg-type]
        return event

    async def search_events(
        self,
        conversation_id: UUID,
        kind__eq: EventKind | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
        sort_order: EventSortOrder = EventSortOrder.TIMESTAMP,
        page_id: str | None = None,
        limit: int = 100,
    ) -> EventPage:
        """Search events matching the given filters."""
        prefix = await self.get_conversation_path(conversation_id)
        events = await self._load_all_events(prefix, regenerate=True)

        # Convert datetime filters to ISO strings so they can be compared
        # against event.timestamp (which is stored as an ISO 8601 string).
        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None

        items: list[Event] = []
        for event in events:
            if kind__eq and event.kind != kind__eq:
                continue
            if timestamp_gte_str and event.timestamp < timestamp_gte_str:
                continue
            if timestamp_lt_str and event.timestamp >= timestamp_lt_str:
                continue
            items.append(event)

        if sort_order:
            items.sort(
                key=lambda e: e.timestamp,
                reverse=(sort_order == EventSortOrder.TIMESTAMP_DESC),
            )

        # Apply pagination to items (not paths)
        start_offset = 0
        next_page_id = None
        if page_id:
            start_offset = int(page_id)
            items = items[start_offset:]
        if len(items) > limit:
            next_page_id = str(start_offset + limit)
            items = items[:limit]

        return EventPage(items=items, next_page_id=next_page_id)

    async def iter_events_for_export(
        self, conversation_id: UUID
    ) -> AsyncGenerator[Event, None]:
        """Iterate all events once in timestamp order for trajectory export."""
        prefix = await self.get_conversation_path(conversation_id)
        events = await self._load_all_events(prefix, regenerate=True)
        for event in events:
            yield event

    async def count_events(
        self,
        conversation_id: UUID,
        kind__eq: EventKind | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> int:
        """Count events matching the given filters."""
        if not (kind__eq or timestamp__gte or timestamp__lt):
            # No filters: count is just the total event count.  We still load via
            # batches (which lazy-build them once) so the count stays accurate as
            # events stream in, and so the no-filter path benefits from the same
            # caching as search.
            prefix = await self.get_conversation_path(conversation_id)
            all_events = await self._load_all_events(prefix, regenerate=True)
            return len(all_events)

        event_stream = page_iterator(
            self.search_events,
            conversation_id=conversation_id,
            kind__eq=kind__eq,
            timestamp__gte=timestamp__gte,
            timestamp__lt=timestamp__lt,
        )
        result = 0
        async for event in event_stream:
            result += 1
        return result

    async def save_event(self, conversation_id: UUID, event: Event):
        if isinstance(event.id, str):
            id_hex = event.id.replace('-', '')
        else:
            id_hex = event.id.hex  # type: ignore[unreachable]
        path = (await self.get_conversation_path(conversation_id)) / f'{id_hex}.json'
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._store_event, path, event)

    async def batch_get_events(
        self, conversation_id: UUID, event_ids: list[UUID]
    ) -> list[Event | None]:
        """Given a list of ids, get events (Or none for any which were not found)."""
        return await asyncio.gather(
            *[self.get_event(conversation_id, event_id) for event_id in event_ids]
        )
