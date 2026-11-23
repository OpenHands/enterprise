import hashlib
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

if TYPE_CHECKING:
    from openhands.agent_server.persistence.trajectory_archive import (
        Head,
        ObjectRef,
    )


class GoogleCloudArchive:
    def __init__(self, bucket: storage.Bucket, prefix: str) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip('/')
        if self._prefix:
            self._check_key(self._prefix)

    @staticmethod
    def _check_key(key: str) -> None:
        path = PurePosixPath(key)
        if (
            not path.parts
            or path.is_absolute()
            or '..' in path.parts
            or str(path) != key
            or any(char in key for char in '\\#?*[]')
        ):
            raise ValueError('Invalid trajectory object key')

    def _blob(self, key: str, generation: int | None = None) -> storage.Blob:
        self._check_key(key)
        name = f'{self._prefix}/{key}' if self._prefix else key
        return self._bucket.blob(name, generation=generation)

    def put(self, key: str, data: bytes) -> 'ObjectRef':
        from openhands.agent_server.persistence.trajectory_archive import ObjectRef

        blob = self._blob(key)
        try:
            blob.upload_from_string(data, if_generation_match=0, timeout=30)
        except PreconditionFailed:
            existing = self.head(key)
            if existing is None or existing.data != data:
                raise ValueError('Immutable trajectory object conflicts') from None
            generation = existing.revision
        else:
            if blob.generation is None:
                raise ValueError('Trajectory object is missing its generation')
            generation = str(blob.generation)
        return ObjectRef(
            key=key,
            generation=generation,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )

    def get(self, ref: 'ObjectRef') -> bytes:
        if ref.generation is None:
            raise ValueError('A generation is required to read a GCS trajectory')
        generation = int(ref.generation)
        try:
            return self._blob(ref.key, generation).download_as_bytes(
                if_generation_match=generation, timeout=30
            )
        except (NotFound, PreconditionFailed) as exc:
            raise FileNotFoundError('Pinned trajectory object is unavailable') from exc

    def head(self, key: str) -> 'Head | None':
        from openhands.agent_server.persistence.trajectory_archive import Head

        blob = self._blob(key)
        try:
            blob.reload(timeout=30)
        except NotFound:
            return None
        generation = blob.generation
        if generation is None:
            raise ValueError('Trajectory object is missing its generation')
        try:
            data = self._blob(key, generation).download_as_bytes(
                if_generation_match=generation, timeout=30
            )
        except (NotFound, PreconditionFailed) as exc:
            raise OSError('Trajectory head changed during read') from exc
        return Head(str(generation), data)

    def advance(self, key: str, previous: 'Head | None', data: bytes) -> None:
        generation = int(previous.revision) if previous else 0
        try:
            self._blob(key).upload_from_string(
                data, if_generation_match=generation, timeout=30
            )
        except PreconditionFailed as exc:
            current = self.head(key)
            if current is None or current.data != data:
                raise OSError('Trajectory head changed during publish') from exc
