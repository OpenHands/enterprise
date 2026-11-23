import argparse
import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage
from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator

if TYPE_CHECKING:
    from openhands.agent_server.persistence.trajectory_archive import (
        Head,
        ObjectRef,
        ObjectStore,
    )


logger = logging.getLogger(__name__)


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


class Enrollment(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    conversation_id: UUID
    run_ids: set[str]
    origin: Literal['interactive', 'automation', 'evaluation', 'unknown']
    legacy_inventory: dict | None = None

    @field_validator('run_ids')
    @classmethod
    def validate_run_ids(cls, run_ids: set[str]) -> set[str]:
        if not run_ids or any(UUID(run_id).hex != run_id for run_id in run_ids):
            raise ValueError('Expected independently enrolled, canonical run IDs')
        return run_ids


def archive_once(
    enrollments: list[Enrollment],
    spool: Path,
    witness: Path,
    store: 'ObjectStore',
    emit: Callable[[dict], None],
    *,
    legacy_store: 'ObjectStore',
) -> bool:
    from openhands.agent_server.persistence.trajectory_archive import (
        ObjectRef,
        coverage,
        read_seal,
        read_segments,
        seal_journal,
    )
    from openhands.agent_server.persistence.trajectory_validation import (
        CandidateSource,
        compare,
        read_witness,
        verify_candidate,
    )
    from openhands.sdk.observability.trajectory_journal import Journal

    if spool.resolve() == witness.resolve():
        raise ValueError('Journal and witness require separate spools')
    if len({item.conversation_id for item in enrollments}) != len(enrollments):
        raise ValueError('Duplicate conversation in trajectory enrollment')

    ok = True
    for item in enrollments:
        conversation = str(item.conversation_id)
        report = {'conversation_id': conversation, 'status': 'hold'}
        try:
            filename = f'{conversation}.sqlite3'
            path = spool / filename
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError('Journal spool missing')
            journal = Journal(spool, conversation)
            ref = seal_journal(journal, store)
            snapshot = read_seal(store, ref)
            report.update(seal=ref.model_dump(), through=snapshot.through)
            witness_path = witness / filename
            if witness_path.is_symlink() or not witness_path.is_file():
                raise FileNotFoundError('Witness spool missing')
            admission = read_witness(
                Journal(witness, conversation), item.origin, item.run_ids
            )
            legacy = None
            if item.legacy_inventory is not None:
                legacy = verify_candidate(
                    legacy_store,
                    CandidateSource(
                        source='legacy',
                        store='',
                        archive=ObjectRef.model_validate(item.legacy_inventory),
                    ),
                    conversation,
                )
            verified = compare(
                admission, coverage(read_segments(store, snapshot)), legacy
            )
            eligible = (
                verified['shadow_eligible']
                and legacy is not None
                and bool(legacy.evidence)
                and not (legacy.gaps or verified['stores']['legacy']['conflicting'])
            )
            report.update(status='eligible' if eligible else 'hold')
            data = json.dumps(verified, sort_keys=True, separators=(',', ':')).encode()
            digest = hashlib.sha256(data).hexdigest()
            report['validation'] = store.put(
                f'shadow/v1/{conversation}/reports/{digest}.json', data
            ).model_dump()
            ok &= eligible
        except Exception as exc:
            logger.warning(
                'Trajectory archive for %s failed: %s', conversation, type(exc).__name__
            )
            report.update(status='hold', error=type(exc).__name__)
            ok = False
        emit(report)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--spool', type=Path, required=True)
    parser.add_argument('--witness', type=Path, required=True)
    parser.add_argument('--enrollment', type=Path, required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--legacy-bucket', required=True)
    args = parser.parse_args()

    enrollments: list[Enrollment] = TypeAdapter(list[Enrollment]).validate_json(
        args.enrollment.read_bytes()
    )
    if not enrollments:
        raise ValueError('Expected at least one enrolled conversation')
    if not args.prefix.rstrip('/'):
        parser.error('A private shadow archive prefix is required')
    client = storage.Client()
    store = GoogleCloudArchive(client.bucket(args.bucket), args.prefix)
    legacy = GoogleCloudArchive(client.bucket(args.legacy_bucket), '')
    return (
        0
        if archive_once(
            enrollments,
            args.spool,
            args.witness,
            store,
            lambda report: print(json.dumps(report)),
            legacy_store=legacy,
        )
        else 1
    )


if __name__ == '__main__':
    raise SystemExit(main())
