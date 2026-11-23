import hashlib
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError
from google.api_core.exceptions import (
    GoogleAPICallError,
    GoogleAPIError,
    NotFound,
    PreconditionFailed,
)
from google.cloud import storage

if TYPE_CHECKING:
    from openhands.agent_server.persistence.trajectory_archive import (
        Head,
        ObjectRef,
    )


class _ArchivePrefix:
    def __init__(self, prefix: str) -> None:
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

    def _key(self, key: str) -> str:
        self._check_key(key)
        return f'{self._prefix}/{key}' if self._prefix else key


class GoogleCloudArchive(_ArchivePrefix):
    def __init__(self, bucket: storage.Bucket, prefix: str) -> None:
        super().__init__(prefix)
        self._bucket = bucket

    def _blob(self, key: str, generation: int | None = None) -> storage.Blob:
        return self._bucket.blob(self._key(key), generation=generation)

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
        return self._read_generation(ref.key, int(ref.generation))

    def _read_generation(self, key: str, generation: int) -> bytes:
        try:
            return self._blob(key, generation).download_as_bytes(
                if_generation_match=generation, timeout=30
            )
        except GoogleAPICallError as exc:
            if exc.code not in (404, 410, 412):
                raise
            raise FileNotFoundError('Pinned trajectory object is unavailable') from exc
        except OSError as exc:
            raise GoogleAPIError('GCS trajectory read was interrupted') from exc

    def head(self, key: str) -> 'Head | None':
        from openhands.agent_server.persistence.trajectory_archive import Head

        blob = self._blob(key)
        try:
            blob.reload(timeout=30)
        except NotFound:
            # Object and bucket lookups share 404. Listing only needs object IAM.
            next(
                iter(
                    self._bucket.list_blobs(prefix=blob.name, max_results=1, timeout=30)
                ),
                None,
            )
            return None
        generation = blob.generation
        if generation is None:
            raise ValueError('Trajectory object is missing its generation')
        try:
            data = self._read_generation(key, generation)
        except FileNotFoundError as exc:
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


class S3Archive(_ArchivePrefix):
    def __init__(self, client: Any, bucket: str, prefix: str) -> None:
        super().__init__(prefix)
        self._client = client
        self._bucket = bucket

    def _read(self, key: str, **condition: str) -> tuple[dict, bytes]:
        result = self._client.get_object(
            Bucket=self._bucket, Key=self._key(key), **condition
        )
        with result['Body'] as stream:
            return result, stream.read()

    def _write(self, key: str, data: bytes, **condition: str) -> dict:
        return self._client.put_object(
            Bucket=self._bucket, Key=self._key(key), Body=data, **condition
        )

    def put(self, key: str, data: bytes) -> 'ObjectRef':
        from openhands.agent_server.persistence.trajectory_archive import ObjectRef

        try:
            result = self._write(key, data, IfNoneMatch='*')
        except ClientError as exc:
            if exc.response['ResponseMetadata']['HTTPStatusCode'] != 412:
                raise
            result, existing = self._read(key)
            if existing != data:
                raise ValueError('Immutable trajectory object conflicts') from exc
        version = result.get('VersionId')
        revision = (
            f'version:{version}' if version and version != 'null' else result['ETag']
        )
        return ObjectRef(
            key=key,
            generation=revision,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )

    def get(self, ref: 'ObjectRef') -> bytes:
        if ref.generation is None:
            raise ValueError('A revision is required to read an S3 trajectory')
        if ref.generation.startswith('version:'):
            condition = {'VersionId': ref.generation.removeprefix('version:')}
        else:
            condition = {'IfMatch': ref.generation}
        try:
            _, data = self._read(ref.key, **condition)
        except ClientError as exc:
            if (
                exc.response['Error']['Code'] in ('NoSuchKey', 'NoSuchVersion')
                or exc.response['ResponseMetadata']['HTTPStatusCode'] == 412
            ):
                raise FileNotFoundError(
                    'Pinned trajectory object is unavailable'
                ) from exc
            raise
        return data

    def head(self, key: str) -> 'Head | None':
        from openhands.agent_server.persistence.trajectory_archive import Head

        try:
            result, data = self._read(key)
        except ClientError as exc:
            if exc.response['Error']['Code'] == 'NoSuchKey':
                return None
            raise
        return Head(result['ETag'], data)

    def advance(self, key: str, previous: 'Head | None', data: bytes) -> None:
        condition = {'IfMatch': previous.revision} if previous else {'IfNoneMatch': '*'}
        try:
            self._write(key, data, **condition)
        except ClientError as exc:
            if exc.response['ResponseMetadata']['HTTPStatusCode'] != 412:
                raise
            current = self.head(key)
            if current is None or current.data != data:
                raise OSError('Trajectory head changed during publish') from exc
