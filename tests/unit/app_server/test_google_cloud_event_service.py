"""Tests for the Google Cloud Storage event index primitives."""

import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from openhands.app_server.event.event_parsing import parse_event_payload
from openhands.app_server.event.google_cloud_event_service import (
    EventStreamConflict,
    GoogleCloudEventService,
)


def _service(bucket: MagicMock) -> GoogleCloudEventService:
    return GoogleCloudEventService(
        prefix=Path('users'),
        user_id='test_user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
        bucket=bucket,
    )


def test_invalidate_renames_index_to_stale():
    bucket = MagicMock()
    svc = _service(bucket)
    conversation_path = Path('users/test_user/v1_conversations/abc')

    with patch.object(svc, '_index_exists', return_value=True):
        svc._invalidate_index(conversation_path)

    bucket.rename_blob.assert_called_once_with(
        bucket.blob.return_value, str(svc._index_stale_path(conversation_path))
    )


def test_invalidate_tolerates_a_concurrent_invalidation():
    """Events saved together can all see index.json before one renames it."""
    bucket = MagicMock()
    bucket.rename_blob.side_effect = NotFound('index.json')
    svc = _service(bucket)

    with patch.object(svc, '_index_exists', return_value=True):
        svc._invalidate_index(Path('users/test_user/v1_conversations/abc'))

    bucket.rename_blob.assert_called_once()


@pytest.mark.asyncio
async def test_stream_ack_waits_for_full_storage_and_survives_retry():
    bucket = MagicMock()
    svc = _service(bucket)
    conversation_id = uuid4()
    payloads = [
        {'kind': 'FutureEvent', 'id': str(uuid4()), 'source': 'agent'} for _ in range(3)
    ]
    entries = [
        (seq, payload, parse_event_payload(payload))
        for seq, payload in enumerate(payloads)
    ]
    writes = set()
    checkpoint = -1
    fail = True

    def store(path, seq, payload, event):
        if seq == 1 and fail:
            raise OSError('GCS unavailable')
        writes.add(seq)
        return True

    def advance(data, **kwargs):
        nonlocal checkpoint
        assert writes == {0, 1, 2}
        assert kwargs['if_generation_match'] == 0
        checkpoint = json.loads(data)['acked_through']

    bucket.blob.return_value.upload_from_string.side_effect = advance
    with (
        patch.object(
            svc,
            '_stream_checkpoint',
            side_effect=lambda _: (checkpoint, 0),
        ),
        patch.object(svc, '_invalidate_index'),
        patch.object(svc, '_store_stream_event', side_effect=store),
        patch.object(svc, '_bind_stream_sequence') as verify_replayed,
    ):
        with pytest.raises(OSError, match='GCS unavailable'):
            await svc.save_event_stream_batch(conversation_id, entries)

        assert writes == {0, 2}
        assert await svc.get_event_stream_ack(conversation_id) == -1
        bucket.blob.return_value.upload_from_string.assert_not_called()

        fail = False
        assert await svc.save_event_stream_batch(conversation_id, entries) == [
            entry[2] for entry in entries
        ]
        assert await svc.get_event_stream_ack(conversation_id) == 2
        assert await svc.save_event_stream_batch(conversation_id, entries) == []
        assert verify_replayed.call_count == 3
        bucket.blob.return_value.upload_from_string.assert_called_once()


@pytest.mark.asyncio
async def test_stream_rejects_gap_without_writing_objects():
    bucket = MagicMock()
    svc = _service(bucket)
    payload = {'kind': 'FutureEvent', 'id': str(uuid4()), 'source': 'agent'}
    with patch.object(svc, '_stream_checkpoint', return_value=(-1, 0)):
        with pytest.raises(EventStreamConflict, match='Expected event sequence 0'):
            await svc.save_event_stream_batch(
                uuid4(), [(1, payload, parse_event_payload(payload))]
            )
    bucket.blob.assert_not_called()


def test_stream_rejects_different_event_at_already_bound_sequence():
    bucket = MagicMock()
    blob = bucket.blob.return_value
    blob.upload_from_string.side_effect = PreconditionFailed('Already exists')
    blob.download_as_bytes.return_value = (
        sha256(b'previous payload').hexdigest().encode()
    )
    svc = _service(bucket)

    with pytest.raises(EventStreamConflict, match='Conflicting event at sequence 0'):
        svc._bind_stream_sequence(Path('conversation'), 0, b'conflicting payload')


def test_stream_rejects_existing_event_id_with_other_sequence():
    bucket = MagicMock()
    blob = bucket.blob.return_value
    blob.upload_from_string.side_effect = PreconditionFailed('Already exists')
    blob.metadata = {'event-seq': '0'}
    blob.generation = 5
    payload = {'kind': 'FutureEvent', 'id': str(uuid4()), 'source': 'agent'}

    def reload():
        blob.metadata = {'event-seq': '0'}

    blob.reload.side_effect = reload
    blob.download_as_bytes.return_value = json.dumps(payload).encode()
    svc = _service(bucket)

    with (
        patch.object(svc, '_bind_stream_sequence'),
        pytest.raises(EventStreamConflict, match='Conflicting event at sequence 1'),
    ):
        svc._store_stream_event(
            Path('conversation/event.json'), 1, payload, parse_event_payload(payload)
        )
    blob.patch.assert_not_called()


@pytest.mark.parametrize('stream_bound', [True, False])
def test_legacy_store_refreshes_only_unbound_objects(stream_bound):
    bucket = MagicMock()
    blob = bucket.blob.return_value
    blob.upload_from_string.side_effect = [PreconditionFailed('Already exists'), None]
    blob.metadata = {'event-seq': '0'} if stream_bound else {}
    blob.generation = 5
    blob.metageneration = 2
    svc = _service(bucket)
    event = parse_event_payload(
        {'kind': 'FutureEvent', 'id': str(uuid4()), 'source': 'agent'}
    )

    svc._store_event(Path('conversation/event.json'), event)

    if stream_bound:
        assert blob.upload_from_string.call_count == 1
    else:
        assert blob.upload_from_string.call_count == 2
        assert blob.upload_from_string.call_args.kwargs['if_generation_match'] == 5
        assert blob.upload_from_string.call_args.kwargs['if_metageneration_match'] == 2
