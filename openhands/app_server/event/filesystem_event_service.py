import glob
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Request

from openhands.app_server.event.event_service import EventService, EventServiceInjector
from openhands.app_server.event.event_service_base import (
    BATCH_DIR_NAME,
    EventServiceBase,
    _parse_batch,
    _serialize_batch,
)
from openhands.app_server.services.injector import InjectorState
from openhands.sdk import Event

_logger = logging.getLogger(__name__)


@dataclass
class FilesystemEventService(EventServiceBase):
    """Event service based on file system"""

    limit: int = 500

    def _load_event(self, path: Path) -> Event | None:
        try:
            content = path.read_text()
            content = Event.model_validate_json(content)  # type: ignore[assignment]
            return content  # type: ignore[return-value]
        except Exception:
            if path.exists():
                _logger.exception('Error reading event', stack_info=True)
            return None

    def _store_event(self, path: Path, event: Event):
        path.parent.mkdir(parents=True, exist_ok=True)
        content = event.model_dump_json(indent=2)
        path.write_text(content)

    def _load_batch(self, path: Path) -> list[Event] | None:
        try:
            return _parse_batch(path.read_text())
        except Exception:
            if path.exists():
                _logger.exception('Error reading batch %s', path, stack_info=True)
            return None

    def _store_batch(self, path: Path, events: list[Event]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_serialize_batch(events))

    def _delete_path(self, path: Path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _search_paths(self, prefix: Path, page_id: str | None = None) -> list[Path]:
        # ``glob('{prefix}/*')`` does not descend into the ``events_batch``
        # sub-directory, so we search the conversation directory and the batch
        # directory separately and merge the results.
        event_files = glob.glob(str(prefix / '*.json'))
        batch_files = glob.glob(str(prefix / BATCH_DIR_NAME / '*.json'))
        return [Path(file) for file in event_files + batch_files]


class FilesystemEventServiceInjector(EventServiceInjector):
    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[EventService, None]:
        from openhands.app_server.config import (
            get_app_conversation_info_service,
            get_global_config,
            get_user_context,
        )

        async with (
            get_user_context(state, request) as user_context,
            get_app_conversation_info_service(
                state, request
            ) as app_conversation_info_service,
        ):
            # Set up a service with a path {persistence_dir}/{user_id}/v1_conversations
            prefix = get_global_config().persistence_dir
            user_id = await user_context.get_user_id()

            yield FilesystemEventService(
                prefix=prefix,
                user_id=user_id,
                app_conversation_info_service=app_conversation_info_service,
                app_conversation_info_load_tasks={},
            )
