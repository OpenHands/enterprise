import glob
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Request

from openhands.app_server.event.event_service import EventService, EventServiceInjector
from openhands.app_server.event.event_service_base import (
    INDEX_FILENAME,
    INDEX_STALE_FILENAME,
    EventServiceBase,
    Index,
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

    def _search_paths(self, prefix: Path, page_id: str | None = None) -> list[Path]:
        search_path = f'{prefix}/*'
        files = glob.glob(str(search_path))
        paths = [Path(file) for file in files]
        return paths

    def _index_path(self, conversation_path: Path) -> Path:
        return conversation_path / INDEX_FILENAME

    def _index_stale_path(self, conversation_path: Path) -> Path:
        return conversation_path / INDEX_STALE_FILENAME

    def _index_exists(self, path: Path) -> bool:
        return path.exists()

    def _load_index(self, path: Path) -> Index | None:
        if not path.exists():
            return None
        try:
            with path.open('r') as f:
                data = json.load(f)
            if not isinstance(data, list):
                return None
            return data
        except (json.JSONDecodeError, OSError):
            _logger.warning('Malformed index at %s; will rebuild', path)
            return None

    def _store_index(self, path: Path, index: Index) -> None:
        """Write index atomically via temp file + os.replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(index)
        # tempfile in the same directory so os.replace is atomic on the same FS.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix='.index_', suffix='.tmp'
        )
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(data)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _invalidate_index(self, conversation_path: Path) -> None:
        """Rename index.json -> index_stale.json (atomic, idempotent)."""
        index_path = self._index_path(conversation_path)
        if not index_path.exists():
            return  # already stale/absent
        stale_path = self._index_stale_path(conversation_path)
        # os.replace overwrites the target if it exists and is atomic on POSIX.
        os.replace(index_path, stale_path)


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
