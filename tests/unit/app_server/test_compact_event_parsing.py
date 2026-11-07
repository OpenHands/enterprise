import io
import json
from unittest.mock import MagicMock, patch

import pytest

from openhands.app_server.event.aws_event_service import AwsEventService
from openhands.app_server.event.event_parsing import (
    parse_compact_event_json,
    parse_event_json,
)
from openhands.app_server.event.filesystem_event_service import FilesystemEventService
from openhands.app_server.event.google_cloud_event_service import (
    GoogleCloudEventService,
)
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent


@pytest.fixture
def snapshot():
    return ConversationStateUpdateEvent(
        key='full_state', value={'history': [{'content': ['preserve me']}]}
    ).model_dump(mode='json')


@pytest.mark.parametrize('as_bytes', [False, True])
@pytest.mark.parametrize('value', [None, {}, [1, {'nested': [2]}], 'opaque', 42, True])
def test_compact_snapshot_skips_full_python_json_decoding(snapshot, value, as_bytes):
    snapshot['value'] = value
    raw = json.dumps(snapshot)
    if as_bytes:
        raw = raw.encode()
    with patch(
        'openhands.app_server.event.event_parsing.json.loads',
        side_effect=AssertionError('Decoded a discarded snapshot'),
    ):
        assert parse_compact_event_json(raw) is None


@pytest.mark.parametrize('missing', ['id', 'timestamp', 'source', 'parent_id', 'value'])
def test_compact_snapshot_accepts_existing_sdk_defaults(snapshot, missing):
    snapshot.pop(missing)
    assert isinstance(
        parse_event_json(json.dumps(snapshot)), ConversationStateUpdateEvent
    )
    assert parse_compact_event_json(json.dumps(snapshot)) is None


@pytest.mark.parametrize(
    'extra',
    [
        {'future_field': {'nested': ['retain this']}},
        {'source': 'future-source'},
        {'kind': 'FutureStateEvent'},
        {'key': 'execution_status', 'value': 'RUNNING'},
        {'key': 'future_key', 'value': {'key': 'full_state'}},
    ],
)
def test_compact_parser_preserves_fallback_payloads_and_other_state(snapshot, extra):
    snapshot.update(extra)
    raw = json.dumps(snapshot)
    expected = parse_event_json(raw)
    actual = parse_compact_event_json(raw)
    assert type(actual) is type(expected)
    assert actual.model_dump(mode='json') == expected.model_dump(mode='json')


@pytest.mark.parametrize('extra', [{'id': []}, {'timestamp': {}}, {'parent_id': 23}])
def test_compact_parser_preserves_invalid_metadata_errors(snapshot, extra):
    snapshot.update(extra)
    raw = json.dumps(snapshot)
    with pytest.raises(Exception) as original:
        parse_event_json(raw)
    with pytest.raises(type(original.value)):
        parse_compact_event_json(raw)


@pytest.mark.parametrize(
    'raw',
    [
        '{"kind":"ConversationStateUpdateEvent","key":"full_state","value":',
        '{"kind":"ConversationStateUpdateEvent","key":"full_state"} trailing',
        b'\xff',
    ],
)
def test_compact_parser_preserves_json_errors(raw):
    with pytest.raises((ValueError, UnicodeError)) as original:
        parse_event_json(raw)
    with pytest.raises(type(original.value)):
        parse_compact_event_json(raw)


@pytest.mark.parametrize(
    'suffix',
    [
        '"kind":"FutureStateEvent"',
        '"key":"execution_status"',
        '"source":"future-source"',
        '"value":{"kind":"FutureStateEvent"}',
        '"unknown_field":123',
    ],
)
def test_duplicate_keys_follow_existing_parser(snapshot, suffix):
    raw = json.dumps(snapshot)[:-1] + ',' + suffix + '}'
    expected = parse_event_json(raw)
    actual = parse_compact_event_json(raw)
    if (
        isinstance(expected, ConversationStateUpdateEvent)
        and expected.key == 'full_state'
    ):
        assert actual is None
    else:
        assert actual.model_dump(mode='json') == expected.model_dump(mode='json')


def test_legacy_validator_rejection_retains_original_snapshot(snapshot):
    from openhands.sdk import Event

    validate = Event.model_validate.__func__

    def reject_snapshot(cls, data, **kwargs):
        if cls is Event and data.get('kind') == 'ConversationStateUpdateEvent':
            data.pop('kind')
            raise ValueError('Legacy validator rejected snapshot')
        return validate(cls, data, **kwargs)

    with patch.object(Event, 'model_validate', classmethod(reject_snapshot)):
        actual = parse_compact_event_json(json.dumps(snapshot))
    assert actual.model_dump(mode='json') == snapshot


@pytest.mark.parametrize('backend', ['filesystem', 's3', 'gcs'])
@pytest.mark.parametrize('extra', [{}, {'future_field': True}, {'key': 'stats'}])
def test_storage_compact_reads_preserve_raw_reads(tmp_path, snapshot, backend, extra):
    snapshot.update(extra)
    raw = json.dumps(snapshot)
    path = tmp_path / 'event.json'
    common = dict(
        prefix=tmp_path,
        user_id='user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
    )
    if backend == 'filesystem':
        path.write_text(raw)
        service = FilesystemEventService(**common)
    elif backend == 's3':
        client = MagicMock()
        client.get_object.side_effect = lambda **kwargs: {
            'Body': io.BytesIO(raw.encode())
        }
        service = AwsEventService(**common, s3_client=client, bucket_name='test')
    else:
        bucket = MagicMock()
        bucket.blob.return_value.open.side_effect = lambda mode: io.StringIO(raw)
        service = GoogleCloudEventService(**common, bucket=bucket)

    expected = parse_event_json(raw)
    compact = service._load_compact_event(path)
    if not extra:
        assert compact is None
    else:
        assert compact.model_dump(mode='json') == expected.model_dump(mode='json')
    assert service._load_event(path).model_dump(mode='json') == expected.model_dump(
        mode='json'
    )
