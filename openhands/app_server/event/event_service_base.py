import asyncio
import os
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator
from uuid import UUID

from openhands.agent_server.models import EventPage, EventSortOrder
from openhands.sdk import Event
from openhands.sdk.utils.paging import page_iterator

from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.conversation_paths import V1_CONVERSATIONS_DIR
from openhands.app_server.event.event_service import EventService
from openhands.app_server.event_callback.event_callback_models import EventKind


def _event_load_concurrency() -> int:
    try:
        return max(1, int(os.getenv('EVENT_SERVICE_LOAD_EVENT_CONCURRENCY', '10')))
    except ValueError:
        return 10


# Cap the number of conversations whose sorted index is held in memory. Each
# entry stores only lightweight metadata (timestamp/kind/id/path) per event, so
# a 5k-event conversation costs a few hundred KB. An LRU-eviction keeps the
# process-wide cache bounded for multi-tenant servers.
_EVENT_INDEX_CACHE_MAX_CONVERSATIONS = int(
    os.getenv('EVENT_SERVICE_INDEX_CACHE_MAX', '256')
)


@dataclass
class _EventIndexEntry:
    """Lightweight per-event metadata kept in the sorted index.

    Holding only the fields needed to filter/sort/paginate avoids keeping full
    parsed event payloads for every event in memory.
    """

    timestamp: str
    kind: str
    event_id: str
    path: Path


@dataclass
class _EventIndex:
    """A per-conversation sorted index of event metadata.

    The list is sorted ascending by timestamp. ``TIMESTAMP_DESC`` requests walk
    it backwards. Building it requires loading every event once (to read
    ``timestamp``/``kind``), but the result is cached per conversation and
    invalidated on ``save_event`` so subsequent pages are O(limit) instead of
    O(N) — see OHE-3178.
    """

    entries: list[_EventIndexEntry]


class _EventIndexCache:
    """Process-wide, per-conversation sorted index cache.

    EventServiceBase instances are created per-request by the injectors, so a
    per-instance cache would not survive between requests. This module-level
    cache keyed by conversation path lets the second (and later) page of a
    large conversation skip reloading and re-sorting all N events.

    The cache is invalidated whenever a new event is saved for a conversation.
    Concurrent builders are single-flighted per key so that parallel page
    requests for the same conversation cooperate instead of each loading all
    events.
    """

    def __init__(self, max_conversations: int = _EVENT_INDEX_CACHE_MAX_CONVERSATIONS):
        self._max = max_conversations
        self._cache: OrderedDict[str, _EventIndex] = OrderedDict()
        # Single-flight locks: ensures only one request builds the index for a
        # given conversation; others await the same result.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def get(self, key: str) -> _EventIndex | None:
        value = self._cache.get(key)
        if value is not None:
            # Mark as most-recently used (LRU eviction).
            self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: _EventIndex) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._max:
            evicted_key, _ = self._cache.popitem(last=False)
            # Leave the lock behind; it may be reused for the same conversation
            # later. Stale locks for never-again-seen conversations are bounded
            # by the number of distinct conversations ever seen, which is
            # acceptable.
            self._locks.pop(evicted_key, None)

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()


# Module-level cache shared across all EventServiceBase instances in the process.
_event_index_cache = _EventIndexCache()


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

    async def _get_or_build_event_index(self, conversation_path: Path) -> _EventIndex:
        """Return the cached sorted index for a conversation, building it if needed.

        The index is keyed by the conversation's storage path (which encodes
        prefix + user_id + conversation_id), so different users and conversations
        never share entries. Building it loads every event once to read
        ``timestamp``/``kind``/``id``; the result is cached per conversation and
        invalidated on ``save_event`` so subsequent page requests are O(limit)
        instead of O(N) — see OHE-3178.

        Concurrent builders are single-flighted: parallel page requests for the
        same conversation cooperate on one index build rather than each loading
        all events.
        """
        key = str(conversation_path)
        cached = _event_index_cache.get(key)
        if cached is not None:
            return cached

        # Single-flight: only one request builds the index for this conversation;
        # concurrent waiters share the result. The cache itself is checked again
        # inside the lock to handle the race where another request built it
        # while we waited for the lock.
        lock = _event_index_cache._lock(key)
        async with lock:
            cached = _event_index_cache.get(key)
            if cached is not None:
                return cached
            loop = asyncio.get_running_loop()
            paths = await loop.run_in_executor(
                None, self._search_paths, conversation_path
            )
            # _load_events_from_paths preserves input order (asyncio.gather), so
            # zipping paths with loaded events gives each event its actual storage
            # path without reconstructing it from the id.
            events = await self._load_events_from_paths(paths)
            entries: list[_EventIndexEntry] = []
            for path, event in zip(paths, events, strict=True):
                if not event:
                    continue
                entries.append(
                    _EventIndexEntry(
                        timestamp=event.timestamp,
                        kind=event.kind,
                        event_id=str(event.id),
                        path=path,
                    )
                )
            entries.sort(key=lambda e: e.timestamp)
            index = _EventIndex(entries=entries)
            _event_index_cache.set(key, index)
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

        Performance (OHE-3178): instead of loading and re-sorting every event on
        every page request, this builds (once per conversation, cached and
        invalidated on save) a sorted index of lightweight per-event metadata
        (timestamp/kind/id/path). Filtering and pagination run over that in-memory
        index in pure Python with no I/O, and only the ``limit`` event payloads
        for the requested page are loaded from storage. The first page for a
        large conversation still pays a one-time O(N) load to build the index;
        every subsequent page is O(limit).
        """
        conversation_path = await self.get_conversation_path(conversation_id)
        index = await self._get_or_build_event_index(conversation_path)

        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None

        # Filter the index entries (no I/O, no event-body parsing).
        filtered: list[_EventIndexEntry] = []
        for entry in index.entries:
            if kind__eq and entry.kind != kind__eq:
                continue
            if timestamp_gte_str and entry.timestamp < timestamp_gte_str:
                continue
            if timestamp_lt_str and entry.timestamp >= timestamp_lt_str:
                continue
            filtered.append(entry)

        if sort_order == EventSortOrder.TIMESTAMP_DESC:
            filtered.reverse()

        # Apply integer-offset pagination (matches the previous page_id semantics
        # so existing clients keep working). page_id is the offset into the
        # filtered+sorted list.
        start_offset = int(page_id) if page_id else 0
        page_entries = filtered[start_offset : start_offset + limit]
        next_page_id = None
        if start_offset + limit < len(filtered):
            next_page_id = str(start_offset + limit)

        # Load only the event payloads for this page.
        paths = [entry.path for entry in page_entries]
        loaded = await self._load_events_from_paths(paths)
        items: list[Event] = [event for event in loaded if event]

        return EventPage(items=items, next_page_id=next_page_id)

    async def iter_events_for_export(
        self, conversation_id: UUID
    ) -> AsyncGenerator[Event, None]:
        """Iterate all events once in timestamp order for trajectory export.

        Uses the cached sorted index so repeated exports (and exports following a
        paginated search) do not reload and re-sort the full event set.
        """
        conversation_path = await self.get_conversation_path(conversation_id)
        index = await self._get_or_build_event_index(conversation_path)
        # Load all events in index order. Export needs every event, so load them
        # all, but reuse the cached sort order and (for the common case where a
        # search already primed the cache) avoid re-enumerating storage.
        paths = [entry.path for entry in index.entries]
        loaded = await self._load_events_from_paths(paths)
        for event in loaded:
            if event:
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

        events = page_iterator(
            self.search_events,
            conversation_id=conversation_id,
            kind__eq=kind__eq,
            timestamp__gte=timestamp__gte,
            timestamp__lt=timestamp__lt,
        )
        result = 0
        async for event in events:
            result += 1
        return result

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
        conversation_path = await self.get_conversation_path(conversation_id)
        path = conversation_path / f'{id_hex}.json'
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._store_event, path, event)
        # Invalidate the cached index so the next search rebuilds it with the
        # new event included.
        _event_index_cache.invalidate(str(conversation_path))

    async def batch_get_events(
        self, conversation_id: UUID, event_ids: list[UUID]
    ) -> list[Event | None]:
        """Given a list of ids, get events (Or none for any which were not found)."""
        return await asyncio.gather(
            *[self.get_event(conversation_id, event_id) for event_id in event_ids]
        )
