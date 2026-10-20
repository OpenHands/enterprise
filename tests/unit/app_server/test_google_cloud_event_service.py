"""Tests for the Google Cloud Storage event index primitives."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import NotFound

from openhands.app_server.event.google_cloud_event_service import (
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
