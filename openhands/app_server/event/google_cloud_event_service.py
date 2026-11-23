"""Google Cloud Storage-based EventService implementation."""

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, AsyncGenerator, Iterator
from uuid import UUID

from fastapi import Request
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage
from google.cloud.storage.blob import Blob
from google.cloud.storage.bucket import Bucket
from google.cloud.storage.client import Client

from openhands.app_server.config import get_app_conversation_info_service
from openhands.app_server.event.event_parsing import (
    parse_event_json,
    parse_event_payload,
)
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


class EventStreamConflict(Exception):
    pass


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
            return parse_event_json(json_data)
        except NotFound:
            return None
        except Exception:
            _logger.exception(f'Error reading event from {path}', stack_info=True)
            return None

    # TODO: Remove _store_event and save_event_stream_batch after reader cutover,
    # the rollback window, and verified migration.
    # https://github.com/OpenHands/software-agent-sdk/issues/7036
    def _store_event(self, path: Path, event: Event):
        blob = self.bucket.blob(str(path))
        data = event.model_dump_json()
        try:
            blob.upload_from_string(
                data, content_type='application/json', if_generation_match=0
            )
            return
        except PreconditionFailed:
            blob.reload()

        if 'event-seq' not in (blob.metadata or {}):
            blob.upload_from_string(
                data,
                content_type='application/json',
                if_generation_match=blob.generation,
                if_metageneration_match=blob.metageneration,
            )

    def _stream_checkpoint(self, conversation_path: Path) -> tuple[int, int]:
        blob = self.bucket.blob(str(self._checkpoint_path(conversation_path)))
        try:
            blob.reload()
        except NotFound:
            return -1, 0
        data = json.loads(blob.download_as_bytes(if_generation_match=blob.generation))
        ack = data['acked_through']
        if type(ack) is not int or ack < 0 or blob.generation is None:
            raise ValueError('Invalid event stream checkpoint')
        return ack, blob.generation

    @staticmethod
    def _checkpoint_path(conversation_path: Path) -> Path:
        return conversation_path / '.event-stream' / 'checkpoint'

    async def get_event_stream_ack(self, conversation_id: UUID) -> int:
        path = await self.get_conversation_path(conversation_id)
        ack, _ = await asyncio.to_thread(self._stream_checkpoint, path)
        return ack

    def _bind_stream_sequence(
        self, conversation_path: Path, seq: int, encoded: bytes
    ) -> None:
        path = self._checkpoint_path(conversation_path).parent / str(seq)
        blob = self.bucket.blob(str(path))
        digest = sha256(encoded).hexdigest().encode()
        try:
            blob.upload_from_string(digest, if_generation_match=0)
        except PreconditionFailed:
            if blob.download_as_bytes() != digest:
                raise EventStreamConflict(
                    f'Conflicting event at sequence {seq}'
                ) from None

    def _store_stream_event(
        self, path: Path, seq: int, payload: dict[str, Any], event: Event
    ) -> bool:
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        self._bind_stream_sequence(path.parent, seq, encoded)
        blob = self.bucket.blob(str(path))
        blob.metadata = {'event-seq': str(seq)}
        try:
            blob.upload_from_string(
                encoded,
                content_type='application/json',
                if_generation_match=0,
            )
            return True
        except PreconditionFailed:
            blob.reload()
            previous_seq = (blob.metadata or {}).get('event-seq')
            generation = blob.generation
            existing = json.loads(
                blob.download_as_bytes(if_generation_match=generation)
            )
            legacy = (blob.metadata or {}).get('event-stream-legacy') == 'true'
            if previous_seq == str(seq) and not legacy and existing == payload:
                return True
            if (previous_seq is None or (previous_seq == str(seq) and legacy)) and (
                existing == payload
                or parse_event_payload(existing).model_dump(mode='json')
                == event.model_dump(mode='json')
            ):
                if legacy:
                    return False
                blob.metadata = {
                    **(blob.metadata or {}),
                    'event-seq': str(seq),
                    'event-stream-legacy': 'true',
                }
                blob.patch(
                    if_generation_match=generation,
                    if_metageneration_match=blob.metageneration,
                )
                return False
            raise EventStreamConflict(f'Conflicting event at sequence {seq}') from None

    async def save_event_stream_batch(
        self,
        conversation_id: UUID,
        entries: Sequence[tuple[int, dict[str, Any], Event]],
    ) -> list[Event]:
        path = await self.get_conversation_path(conversation_id)
        ack, generation = await asyncio.to_thread(self._stream_checkpoint, path)
        first, through = entries[0][0], entries[-1][0]
        if first > ack + 1:
            raise EventStreamConflict(f'Expected event sequence {ack + 1}')
        retained = entries[: max(0, ack + 1 - first)]
        await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._bind_stream_sequence,
                    path,
                    seq,
                    json.dumps(payload, sort_keys=True, separators=(',', ':')).encode(),
                )
                for seq, payload, _ in retained
            )
        )
        if through <= ack:
            return []

        pending = entries[ack + 1 - first :]
        created = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._store_stream_event,
                    self._event_id_to_path(path, event.id.replace('-', '')),
                    seq,
                    payload,
                    event,
                )
                for seq, payload, event in pending
            ),
            return_exceptions=True,
        )
        for result in created:
            if isinstance(result, BaseException):
                raise result

        await asyncio.to_thread(self._invalidate_index, path)
        blob = self.bucket.blob(str(self._checkpoint_path(path)))
        try:
            await asyncio.to_thread(
                blob.upload_from_string,
                json.dumps({'acked_through': through}),
                content_type='application/json',
                if_generation_match=generation,
            )
        except PreconditionFailed:
            ack, _ = await asyncio.to_thread(self._stream_checkpoint, path)
            if ack < through:
                raise EventStreamConflict(
                    f'Expected event sequence {ack + 1}'
                ) from None
            return []
        return [
            event
            for (_, _, event), result in zip(pending, created, strict=True)
            if result
        ]

    def _search_paths(self, prefix: Path, page_id: str | None = None) -> list[Path]:
        """Search paths."""
        blobs: Iterator[Blob] = self.bucket.list_blobs(
            page_token=page_id, prefix=f'{prefix}/', delimiter='/'
        )
        return [Path(blob.name) for blob in blobs]

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
        try:
            self.bucket.rename_blob(index_blob, str(stale_path))
        except NotFound:
            pass  # Events saved together invalidate concurrently; another got here first.


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
