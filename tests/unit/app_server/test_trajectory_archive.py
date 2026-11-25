from unittest.mock import Mock

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed, from_http_status
from google.cloud import storage

from openhands.app_server.trajectory_archive import GoogleCloudArchive


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


def test_missing_gcs_bucket_does_not_look_like_an_empty_archive():
    pytest.importorskip('openhands.agent_server.persistence.trajectory_archive')
    bucket = Mock(spec=storage.Bucket)
    bucket.blob.return_value.reload.side_effect = NotFound('object lookup returned 404')
    store = GoogleCloudArchive(bucket, 'private/canary')
    bucket.list_blobs.return_value = []
    assert store.head('head.json') is None
    bucket.list_blobs.side_effect = NotFound('no such bucket')
    with pytest.raises(NotFound, match='no such bucket'):
        store.head('head.json')


@pytest.mark.parametrize('status', [404, 410])
def test_unavailable_pinned_generation_is_reported_as_gap(status):
    archive = pytest.importorskip(
        'openhands.agent_server.persistence.trajectory_archive'
    )
    bucket = Mock(spec=storage.Bucket)
    bucket.blob.return_value.download_as_bytes.side_effect = from_http_status(
        status, 'missing generation'
    )
    store = GoogleCloudArchive(bucket, 'private/canary')
    ref = archive.ObjectRef(key='segment', generation='17', sha256='0' * 64, size=1)
    with pytest.raises(archive.ArchiveGap, match='generation 17'):
        archive.verified_bytes(store, ref)
    bucket.blob.assert_called_once_with('private/canary/segment', generation=17)
