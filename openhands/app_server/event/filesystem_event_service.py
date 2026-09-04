import glob
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Request
from openhands.sdk import Event

from openhands.app_server.event.event_service import EventService, EventServiceInjector
from openhands.app_server.event.event_service_base import EventPath, EventServiceBase
from openhands.app_server.services.injector import InjectorState

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

    def _search_paths(self, prefix: Path) -> list[EventPath]:
        search_path = f'{prefix}/*'
        files = glob.glob(str(search_path))
        results: list[EventPath] = []
        for file in files:
            p = Path(file)
            try:
                mtime = os.stat(p).st_mtime
            except OSError:
                # File disappeared between glob and stat; skip it.
                continue
            results.append(EventPath(path=p, mtime=mtime))
        return results


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
