import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
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

_logger = logging.getLogger(__name__)

# An index entry is [event_id, timestamp, kind]. event_id is stored without
# dashes so it maps directly to the stored filename (see _event_id_to_path).
# timestamp is the ISO-8601 string from Event.timestamp; kind is the event
# subclass name (Event.kind). These three fields are sufficient for every
# current filter (kind__eq, timestamp__gte/lt) and sort order.
IndexEntry = list[str]  # [event_id, timestamp, kind]
Index = list[IndexEntry]

INDEX_FILENAME = 'index.json'
INDEX_STALE_FILENAME = 'index_stale.json'


def _event_load_concurrency() -> int:
    try:
        return max(1, int(os.getenv('EVENT_SERVICE_LOAD_EVENT_CONCURRENCY', '10')))
    except ValueError:
        return 10


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

    @abstractmethod
    def _load_event(self, path: Path) -> Event | None:
        """Get the event at the path given."""

    @abstractmethod
    def _store_event(self, path: Path, event: Event):
        """Store the event given at the path given."""

    @abstractmethod
    def _search_paths(self, prefix: Path) -> list[Path]:
        """Search paths."""

    # -- Index storage primitives (implemented per backend) ----------------
    # An index is a JSON file ([[event_id, timestamp, kind], ...]) stored
    # next to the events in a conversation directory. Each backend provides
    # its own I/O primitives because the operations differ (atomic rename
    # on the filesystem vs copy+delete on object stores). All paths are
    # absolute keys/paths within the backend's namespace.

    @abstractmethod
    def _index_path(self, conversation_path: Path) -> Path:
        """Return the path of the index file for a conversation directory."""

    @abstractmethod
    def _index_stale_path(self, conversation_path: Path) -> Path:
        """Return the path of the stale index file for a conversation directory."""

    @abstractmethod
    def _index_exists(self, path: Path) -> bool:
        """Whether an index file exists at the given path."""

    @abstractmethod
    def _load_index(self, path: Path) -> Index | None:
        """Load and parse an index file. Returns None if missing or malformed."""

    @abstractmethod
    def _store_index(self, path: Path, index: Index) -> None:
        """Write an index file to the given path."""

    @abstractmethod
    def _invalidate_index(self, conversation_path: Path) -> None:
        """Atomically (best-effort) rename index.json -> index_stale.json.

        Implementations must be idempotent: if index.json is absent this is
        a no-op. If index_stale.json already exists it should be overwritten
        (the newer snapshot wins) rather than erroring.
        """

    async def _load_events_from_paths(self, paths: list[Path]) -> list[Event | None]:
        loop = asyncio.get_running_loop()
        semaphore = asyncio.Semaphore(_event_load_concurrency())

        async def load_event(path: Path) -> Event | None:
            async with semaphore:
                return await loop.run_in_executor(None, self._load_event, path)

        return await asyncio.gather(*(load_event(path) for path in paths))

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

    # -- Index helpers ------------------------------------------------------

    def _event_id_to_path(self, conversation_path: Path, event_id: str) -> Path:
        """Derive the stored-event path from an event id in an index entry.

        The index stores event ids without dashes so they map directly to the
        filenames written by save_event.
        """
        return conversation_path / f'{event_id}.json'

    def _filter_index(
        self,
        index: Index,
        kind__eq: EventKind | None,
        timestamp_gte_str: str | None,
        timestamp_lt_str: str | None,
    ) -> list[IndexEntry]:
        """Return index entries matching the given filters."""
        result = []
        for entry in index:
            _, timestamp, kind = entry
            if kind__eq and kind != kind__eq:
                continue
            if timestamp_gte_str and timestamp < timestamp_gte_str:
                continue
            if timestamp_lt_str and timestamp >= timestamp_lt_str:
                continue
            result.append(entry)
        return result

    def _sort_index(
        self, entries: list[IndexEntry], sort_order: EventSortOrder
    ) -> list[IndexEntry]:
        """Sort index entries by timestamp."""
        if not sort_order:
            return entries
        return sorted(
            entries,
            key=lambda e: e[1],
            reverse=(sort_order == EventSortOrder.TIMESTAMP_DESC),
        )

    def _entry_for_event(self, event: Event) -> IndexEntry:
        """Build an index entry from a loaded event."""
        if isinstance(event.id, str):
            id_hex = event.id.replace('-', '')
        else:
            id_hex = event.id.hex  # type: ignore[unreachable]
        return [id_hex, event.timestamp, event.kind]

    async def _get_or_rebuild_index(self, conversation_path: Path) -> Index:
        """Return a fresh index for a conversation, rebuilding if needed.

        Reader rule (lock-free, safe under append-only data):
        - index.json present & valid  -> use it, ignore index_stale.json
        - else index_stale.json present -> seed from it, scan diff, write index.json
        - else (neither)              -> full scan, write index.json
        Only the rebuild paths write index.json; the fresh-read path never writes.
        """
        loop = asyncio.get_running_loop()
        index_path = self._index_path(conversation_path)
        if await loop.run_in_executor(None, self._index_exists, index_path):
            index = await loop.run_in_executor(None, self._load_index, index_path)
            if index is not None:
                return index
            # Present but malformed -> treat as missing and rebuild below.

        return await self._rebuild_index(conversation_path)

    async def _rebuild_index(self, conversation_path: Path) -> Index:
        """Rebuild and persist the index.

        Seeds from index_stale.json if present, then scans for any event files
        not already in the seed and loads only those. Deduplicates by event id.
        Writes index.json.
        """
        loop = asyncio.get_running_loop()
        stale_path = self._index_stale_path(conversation_path)
        seeded: dict[str, IndexEntry] = {}
        if await loop.run_in_executor(None, self._index_exists, stale_path):
            stale = await loop.run_in_executor(None, self._load_index, stale_path)
            if stale:
                for entry in stale:
                    seeded[entry[0]] = entry

        # Scan all event files to find ids missing from the seed.
        paths = await loop.run_in_executor(None, self._search_paths, conversation_path)
        # Build the set of ids the seed already knows about, and the list of
        # paths whose events are not yet indexed.
        known_ids = set(seeded.keys())
        # Index files are not events; exclude them by filename.
        missing_paths = [
            p
            for p in paths
            if p.name not in (INDEX_FILENAME, INDEX_STALE_FILENAME)
            and p.stem not in known_ids
        ]

        if missing_paths:
            loaded = await self._load_events_from_paths(missing_paths)
            for event in loaded:
                if event is None:
                    continue
                entry = self._entry_for_event(event)
                seeded[entry[0]] = entry

        index = list(seeded.values())
        index_path = self._index_path(conversation_path)
        await loop.run_in_executor(None, self._store_index, index_path, index)
        return index

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
        """Search events matching the given filters.

        Uses the per-conversation index to filter/sort in memory and loads only
        the events for the requested page instead of every event in the
        conversation.
        """
        conversation_path = await self.get_conversation_path(conversation_id)
        index = await self._get_or_rebuild_index(conversation_path)

        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None

        entries = self._filter_index(
            index, kind__eq, timestamp_gte_str, timestamp_lt_str
        )
        entries = self._sort_index(entries, sort_order)

        # Apply pagination to the index entries (not loaded events).
        start_offset = 0
        next_page_id = None
        if page_id:
            start_offset = int(page_id)
            entries = entries[start_offset:]
        if len(entries) > limit:
            next_page_id = str(start_offset + limit)
            entries = entries[:limit]

        # Load only the events for this page.
        paths = [
            self._event_id_to_path(conversation_path, entry[0]) for entry in entries
        ]
        loaded = await self._load_events_from_paths(paths)
        items = [event for event in loaded if event is not None]
        # Preserve the index order (concurrent load may reorder results).
        by_id = {
            event.id.replace('-', '')
            if isinstance(event.id, str)
            else event.id.hex: event
            for event in items
        }  # type: ignore[union-attr]
        items = [by_id[entry[0]] for entry in entries if entry[0] in by_id]

        return EventPage(items=items, next_page_id=next_page_id)

    async def iter_events_for_export(
        self, conversation_id: UUID
    ) -> AsyncGenerator[Event, None]:
        """Iterate all events once in timestamp order for trajectory export."""
        conversation_path = await self.get_conversation_path(conversation_id)
        index = await self._get_or_rebuild_index(conversation_path)
        entries = self._sort_index(index, EventSortOrder.TIMESTAMP)
        paths = [
            self._event_id_to_path(conversation_path, entry[0]) for entry in entries
        ]
        loaded = await self._load_events_from_paths(paths)
        by_id = {
            event.id.replace('-', '')
            if isinstance(event.id, str)
            else event.id.hex: event
            for event in loaded
            if event is not None
        }  # type: ignore[union-attr]
        for entry in entries:
            event = by_id.get(entry[0])
            if event is not None:
                yield event

    async def count_events(
        self,
        conversation_id: UUID,
        kind__eq: EventKind | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> int:
        """Count events matching the given filters."""
        # If we are not filtering, we can simply count the paths
        if not (kind__eq or timestamp__gte or timestamp__lt):
            conversation_path = await self.get_conversation_path(conversation_id)
            result = await self._count_events_no_filter(conversation_path)
            return result

        # Filtered count: use the index and count matching entries in memory,
        # avoiding any event loads.
        conversation_path = await self.get_conversation_path(conversation_id)
        index = await self._get_or_rebuild_index(conversation_path)
        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None
        entries = self._filter_index(
            index, kind__eq, timestamp_gte_str, timestamp_lt_str
        )
        return len(entries)

    async def _count_events_no_filter(self, conversation_path: Path) -> int:
        """Count all event files in the conversation directory without filtering."""
        loop = asyncio.get_running_loop()
        paths = await loop.run_in_executor(None, self._search_paths, conversation_path)
        return len(paths)

    async def save_event(self, conversation_id: UUID, event: Event):
        if isinstance(event.id, str):
            id_hex = event.id.replace('-', '')
        else:
            id_hex = event.id.hex  # type: ignore[unreachable]
        path = (await self.get_conversation_path(conversation_id)) / f'{id_hex}.json'
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._store_event, path, event)
        # Invalidate the index so the next search rebuilds it. Idempotent: a
        # no-op if index.json is already absent (already stale). This is the
        # only writer of the stale marker.
        conversation_path = path.parent
        await loop.run_in_executor(None, self._invalidate_index, conversation_path)

    async def batch_get_events(
        self, conversation_id: UUID, event_ids: list[UUID]
    ) -> list[Event | None]:
        """Given a list of ids, get events (Or none for any which were not found)."""
        return await asyncio.gather(
            *[self.get_event(conversation_id, event_id) for event_id in event_ids]
        )
