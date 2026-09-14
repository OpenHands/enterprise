"""Google Cloud Storage-based EventService implementation."""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import AsyncGenerator, Iterator

from fastapi import Request
from google.api_core.exceptions import NotFound
from google.cloud import storage
from google.cloud.storage.blob import Blob
from google.cloud.storage.bucket import Bucket
from google.cloud.storage.client import Client

from openhands.app_server.config import get_app_conversation_info_service
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


@lru_cache(maxsize=1)
def _get_shared_storage_client() -> Client:
    """Return a process-wide shared GCS client.

    google.cloud.storage.Client is thread-safe and manages its own urllib3
    connection pool.  Creating one per request leads to pool exhaustion under
    load ("Connection pool is full, discarding connection").
    """
    return storage.Client()


@dataclass
class GoogleCloudEventService(EventServiceBase):
    """Google Cloud Storage-based implementation of EventService."""

    bucket: Bucket

    def _load_event(self, path: Path) -> Event | None:
        """Get the event at the path given."""
        blob: Blob = self.bucket.blob(str(path))
        try:
            with blob.open('r') as f:
                json_data = f.read()
            event = Event.model_validate_json(json_data)
            return event
        except NotFound:
            return None
        except Exception:
            _logger.exception(f'Error reading event from {path}', stack_info=True)
            return None

    def _store_event(self, path: Path, event: Event):
        """Store the event given at the path given."""
        blob: Blob = self.bucket.blob(str(path))
        data = event.model_dump(mode='json')
        with blob.open('w') as f:
            f.write(json.dumps(data, indent=2))

    def _search_paths(self, prefix: Path, page_id: str | None = None) -> list[Path]:
        """Search paths."""
        blobs: Iterator[Blob] = self.bucket.list_blobs(
            page_token=page_id, prefix=str(prefix)
        )
        paths = list(Path(blob.name) for blob in blobs)
        return paths

    def _index_path(self, conversation_path: Path) -> Path:
        return conversation_path / INDEX_FILENAME

    def _index_stale_path(self, conversation_path: Path) -> Path:
        return conversation_path / INDEX_STALE_FILENAME

    def _index_exists(self, path: Path) -> bool:
        return self.bucket.blob(str(path)).exists()

    def _load_index(self, path: Path) -> Index | None:
        blob: Blob = self.bucket.blob(str(path))
        try:
            with blob.open('r') as f:
                json_data = f.read()
            data = json.loads(json_data)
            if not isinstance(data, list):
                return None
            return data
        except NotFound:
            return None
        except (json.JSONDecodeError, ValueError):
            _logger.warning('Malformed index at %s; will rebuild', path)
            return None
        except Exception:
            _logger.exception('Error reading index from %s', path, stack_info=True)
            return None

    def _store_index(self, path: Path, index: Index) -> None:
        blob: Blob = self.bucket.blob(str(path))
        data = json.dumps(index)
        with blob.open('w') as f:
            f.write(data)

    def _invalidate_index(self, conversation_path: Path) -> None:
        """Rename index.json -> index_stale.json via copy+delete (GCS rename is non-atomic)."""
        index_path = self._index_path(conversation_path)
        if not self._index_exists(index_path):
            return  # already stale/absent
        stale_path = self._index_stale_path(conversation_path)
        index_blob = self.bucket.blob(str(index_path))
        # rename is copy+delete under the hood; a concurrent reader may briefly
        # see both files, but the reader rule prefers index.json when present.
        self.bucket.rename_blob(index_blob, str(stale_path))


class GoogleCloudEventServiceInjector(EventServiceInjector):
    bucket_name: str
    prefix: Path = Path('users')

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[EventService, None]:
        from openhands.app_server.config import (
            get_user_context,
        )

        async with (
            get_user_context(state, request) as user_context,
            get_app_conversation_info_service(
                state, request
            ) as app_conversation_info_service,
        ):
            user_id = await user_context.get_user_id()

            bucket_name = self.bucket_name
            bucket: Bucket = _get_shared_storage_client().bucket(bucket_name)

            yield GoogleCloudEventService(
                prefix=self.prefix,
                user_id=user_id,
                app_conversation_info_service=app_conversation_info_service,
                bucket=bucket,
                app_conversation_info_load_tasks={},
            )
