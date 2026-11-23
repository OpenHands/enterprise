import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

from openhands.app_server.trajectory_archive_worker import (
    Enrollment,
    GoogleCloudArchive,
    archive_once,
)


def test_archive_keys_stay_in_configured_prefix():
    bucket = Mock(spec=storage.Bucket)
    store = GoogleCloudArchive(bucket, 'private/canary')
    with pytest.raises(ValueError, match='Invalid trajectory object key'):
        store.advance('../outside', None, b'pointer')
    bucket.blob.assert_not_called()


def test_gcs_retry_reuses_identical_generation():
    pytest.importorskip('openhands.agent_server.persistence.trajectory_archive')
    bucket = Mock(spec=storage.Bucket)
    blob = bucket.blob.return_value
    blob.upload_from_string.side_effect = PreconditionFailed('create already committed')
    blob.generation = 17
    blob.download_as_bytes.return_value = b'archive'
    store = GoogleCloudArchive(bucket, 'private/canary')

    ref = store.put('shadow/v1/archive', b'archive')
    assert ref.generation == '17'
    assert store.get(ref) == b'archive'
    blob.upload_from_string.assert_called_once_with(
        b'archive', if_generation_match=0, timeout=30
    )
    bucket.blob.assert_called_with('private/canary/shadow/v1/archive', generation=17)
    with pytest.raises(ValueError, match='conflicts'):
        store.put('shadow/v1/archive', b'different')


def test_worker_seals_and_compares_an_independent_enrollment(tmp_path):
    archive = pytest.importorskip(
        'openhands.agent_server.persistence.trajectory_archive'
    )
    capture = pytest.importorskip('openhands.sdk.observability.trajectory_capture')
    journal = pytest.importorskip('openhands.sdk.observability.trajectory_journal')
    conversation = uuid4()
    spool, witness = tmp_path / 'journal', tmp_path / 'witness'
    writer = capture.ShadowCapture(
        journal.Journal(spool, str(conversation)),
        journal.Journal(witness, str(conversation)),
        'evaluation',
    )
    ticket = writer.admit()
    assert ticket is not None
    event = {'id': 'event-1', 'kind': 'MessageEvent', 'parent_id': None}
    with (
        capture.use_admission(ticket),
        capture.capture_run(writer, lambda: None, lambda: 'finished'),
    ):
        writer.append(
            'event',
            {
                'event_id': event['id'],
                'event_kind': event['kind'],
                'parent_id': None,
                'action_id': None,
            },
            payloads=(event,),
        )
    store = archive.DirectoryStore(tmp_path / 'archive')
    legacy_event = store.put(
        f'legacy/{conversation.hex}/event1.json', json.dumps(event).encode()
    )
    inventory = store.put(
        'legacy/inventory.json',
        json.dumps(
            {
                'conversation_id': str(conversation),
                'events': [legacy_event.model_dump()],
            }
        ).encode(),
    )
    enrollment = Enrollment(
        conversation_id=conversation,
        run_ids={ticket.run_id},
        origin='evaluation',
        legacy_inventory=inventory.model_dump(),
    )

    reports = []
    assert archive_once(
        [enrollment], spool, witness, store, reports.append, legacy_store=store
    )
    assert archive_once(
        [enrollment], spool, witness, store, reports.append, legacy_store=store
    )
    assert reports[0] == reports[1]
    assert reports[0]['status'] == 'eligible'

    missing = enrollment.model_copy(update={'run_ids': {uuid4().hex}})
    assert not archive_once(
        [missing], spool, witness, store, reports.append, legacy_store=store
    )
    assert reports[-1]['status'] == 'hold'
    report = archive.ObjectRef.model_validate(reports[-1]['validation'])
    assert json.loads(store.get(report))['missing_runs'] == sorted(missing.run_ids)
