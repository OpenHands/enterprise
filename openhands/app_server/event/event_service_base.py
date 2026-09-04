import asyncio
import os
from abc import ABC, abstractmethod
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


@dataclass(frozen=True, slots=True)
class EventPath:
    """A storage-layer path paired with its modification time.

    ``mtime`` is a sortable POSIX timestamp reported by the storage backend's
    listing API (filesystem ``st_mtime``, S3 ``LastModified``, GCS ``updated``).
    It is **not** the event's ``timestamp`` field, but events are append-only
    and never reordered, so ``mtime`` order matches event ``timestamp`` order.
    This lets ``search_events`` sort by time and paginate without loading any
    event bodies — only the events on the requested page are loaded (OHE-3178).
    """

    path: Path
    mtime: float


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
    def _search_paths(self, prefix: Path) -> list[EventPath]:
        """List event paths under ``prefix`` with their storage mtime.

        Each backend's listing API returns the object's last-modified time for
        free (filesystem ``st_mtime``, S3 ``LastModified``, GCS ``updated``).
        Events are append-only and never reordered, so sorting by this mtime
        yields the same order as sorting by the event's ``timestamp`` field —
        without loading any event bodies.
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
        every page request, this sorts the storage-layer path list by file mtime
        (which matches event timestamp order since events are append-only) and
        walks it lazily, loading only one event at a time, applying filters on
        the fly, and stopping as soon as ``limit`` matching events are collected.
        ``page_id`` is an integer offset into the mtime-sorted path list — the
        number of entries to skip before resuming the scan — so each page loads
        only the events it returns plus any non-matching entries it skips over.
        No event bodies are loaded for entries before ``page_id``.
        """
        loop = asyncio.get_running_loop()
        prefix = await self.get_conversation_path(conversation_id)
        event_paths = await loop.run_in_executor(None, self._search_paths, prefix)

        # Sort by storage mtime (ascending). For TIMESTAMP_DESC we reverse so the
        # scan proceeds newest-first; page_id is then an offset into this
        # iteration order.
        event_paths.sort(key=lambda ep: ep.mtime)
        if sort_order == EventSortOrder.TIMESTAMP_DESC:
            event_paths.reverse()

        start_offset = int(page_id) if page_id else 0
        # Clamp out-of-range offsets (e.g. a stale page_id from before events were
        # added/removed) to a safe no-op rather than erroring.
        start_offset = max(0, min(start_offset, len(event_paths)))

        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None

        items: list[Event] = []
        next_page_id: str | None = None
        # Index of the next entry to scan on a subsequent page. Updated as we go
        # so a full page records the resume point right after the last examined
        # entry (whether or not it matched), avoiding re-examining entries.
        resume_index = start_offset
        for i in range(start_offset, len(event_paths)):
            resume_index = i + 1
            event = await loop.run_in_executor(
                None, self._load_event, event_paths[i].path
            )
            if event is None:
                continue
            if kind__eq and event.kind != kind__eq:
                continue
            if timestamp_gte_str and event.timestamp < timestamp_gte_str:
                continue
            if timestamp_lt_str and event.timestamp >= timestamp_lt_str:
                continue
            items.append(event)
            if len(items) >= limit:
                break

        if resume_index < len(event_paths):
            next_page_id = str(resume_index)

        return EventPage(items=items, next_page_id=next_page_id)

    async def iter_events_for_export(
        self, conversation_id: UUID
    ) -> AsyncGenerator[Event, None]:
        """Iterate all events once in timestamp order for trajectory export."""
        loop = asyncio.get_running_loop()
        prefix = await self.get_conversation_path(conversation_id)
        event_paths = await loop.run_in_executor(None, self._search_paths, prefix)
        event_paths.sort(key=lambda ep: ep.mtime)
        for event_path in event_paths:
            event = await loop.run_in_executor(None, self._load_event, event_path.path)
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
        event_paths = await loop.run_in_executor(
            None, self._search_paths, conversation_path
        )
        return len(event_paths)

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
