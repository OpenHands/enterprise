"""Tests for AwsEventService.

This module tests the AWS S3-based implementation of EventService,
focusing on search functionality and S3 operations.
"""

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import botocore.exceptions
import pytest

from openhands.app_server.event import aws_event_service
from openhands.app_server.event.aws_event_service import (
    AwsEventService,
    AwsEventServiceInjector,
)
from openhands.sdk.event import PauseEvent, TokenEvent


@pytest.fixture
def mock_s3_client():
    """Create a mock S3 client."""
    return MagicMock()


@pytest.fixture
def service(mock_s3_client) -> AwsEventService:
    """Create an AwsEventService instance for testing."""
    return AwsEventService(
        prefix=Path('users'),
        user_id='test_user',
        app_conversation_info_service=None,
        s3_client=mock_s3_client,
        bucket_name='test-bucket',
        app_conversation_info_load_tasks={},
    )


@pytest.fixture
def service_no_user(mock_s3_client) -> AwsEventService:
    """Create an AwsEventService instance without user_id."""
    return AwsEventService(
        prefix=Path('users'),
        user_id=None,
        app_conversation_info_service=None,
        s3_client=mock_s3_client,
        bucket_name='test-bucket',
        app_conversation_info_load_tasks={},
    )


def create_token_event() -> TokenEvent:
    """Helper to create a TokenEvent for testing."""
    return TokenEvent(
        source='agent', prompt_token_ids=[1, 2], response_token_ids=[3, 4]
    )


def create_pause_event() -> PauseEvent:
    """Helper to create a PauseEvent for testing."""
    return PauseEvent(source='user')


class TestAwsEventServiceLoadEvent:
    """Test cases for _load_event method."""

    def test_load_event_success(self, service: AwsEventService, mock_s3_client):
        """Test that _load_event successfully loads an event from S3."""
        event = create_token_event()
        json_data = event.model_dump_json()

        # Mock the S3 response
        mock_body = MagicMock()
        mock_body.read.return_value = json_data.encode('utf-8')
        mock_body.__enter__ = MagicMock(return_value=mock_body)
        mock_body.__exit__ = MagicMock(return_value=False)
        mock_s3_client.get_object.return_value = {'Body': mock_body}

        result = service._load_event(Path('some/path/event.json'))

        assert result is not None
        assert result.kind == 'TokenEvent'
        mock_s3_client.get_object.assert_called_once_with(
            Bucket='test-bucket', Key='some/path/event.json'
        )

    def test_load_event_not_found(self, service: AwsEventService, mock_s3_client):
        """Test that _load_event returns None when event doesn't exist."""
        error_response = {'Error': {'Code': 'NoSuchKey', 'Message': 'Not found'}}
        mock_s3_client.get_object.side_effect = botocore.exceptions.ClientError(
            error_response, 'GetObject'
        )

        result = service._load_event(Path('some/path/missing.json'))

        assert result is None

    def test_load_event_other_error(self, service: AwsEventService, mock_s3_client):
        """Test that _load_event returns None and logs error on other S3 errors."""
        error_response = {'Error': {'Code': 'AccessDenied', 'Message': 'Access denied'}}
        mock_s3_client.get_object.side_effect = botocore.exceptions.ClientError(
            error_response, 'GetObject'
        )

        result = service._load_event(Path('some/path/denied.json'))

        assert result is None


class TestAwsEventServiceStoreEvent:
    """Test cases for _store_event method."""

    def test_store_event_success(self, service: AwsEventService, mock_s3_client):
        """Test that _store_event successfully stores an event to S3."""
        event = create_token_event()

        service._store_event(Path('some/path/event.json'), event)

        mock_s3_client.put_object.assert_called_once()
        call_args = mock_s3_client.put_object.call_args
        assert call_args.kwargs['Bucket'] == 'test-bucket'
        assert call_args.kwargs['Key'] == 'some/path/event.json'
        # Verify the body is valid JSON
        body = call_args.kwargs['Body'].decode('utf-8')
        data = json.loads(body)
        assert data['kind'] == 'TokenEvent'


class TestAwsEventServiceSearchPaths:
    """Test cases for _search_paths method."""

    def test_search_paths_returns_paths(self, service: AwsEventService, mock_s3_client):
        """Test that _search_paths returns paths from S3."""
        mock_s3_client.list_objects_v2.return_value = {
            'Contents': [
                {'Key': 'users/test_user/v1_conversations/abc123/event1.json'},
                {'Key': 'users/test_user/v1_conversations/abc123/event2.json'},
            ]
        }

        result = service._search_paths(Path('users/test_user/v1_conversations/abc123'))

        assert len(result) == 2
        assert result[0] == Path('users/test_user/v1_conversations/abc123/event1.json')
        assert result[1] == Path('users/test_user/v1_conversations/abc123/event2.json')

    def test_search_paths_empty_bucket(self, service: AwsEventService, mock_s3_client):
        """Test that _search_paths handles empty results."""
        mock_s3_client.list_objects_v2.return_value = {}

        result = service._search_paths(Path('users/test_user/v1_conversations/abc123'))

        assert len(result) == 0

    def test_search_paths_with_page_id(self, service: AwsEventService, mock_s3_client):
        """Test that _search_paths uses continuation token."""
        mock_s3_client.list_objects_v2.return_value = {
            'Contents': [{'Key': 'event.json'}]
        }

        service._search_paths(Path('prefix'), page_id='continuation_token')

        mock_s3_client.list_objects_v2.assert_called_once_with(
            Bucket='test-bucket',
            Prefix='prefix',
            ContinuationToken='continuation_token',
        )

    def test_search_paths_follows_continuation_tokens(
        self, service: AwsEventService, mock_s3_client
    ):
        """Test that truncated list responses (>1000 keys) are followed to the end."""
        mock_s3_client.list_objects_v2.side_effect = [
            {
                'Contents': [{'Key': 'event1.json'}],
                'IsTruncated': True,
                'NextContinuationToken': 'token-2',
            },
            {
                'Contents': [{'Key': 'event2.json'}],
                'IsTruncated': True,
                'NextContinuationToken': 'token-3',
            },
            {'Contents': [{'Key': 'event3.json'}]},
        ]

        result = service._search_paths(Path('prefix'))

        assert result == [
            Path('event1.json'),
            Path('event2.json'),
            Path('event3.json'),
        ]
        assert mock_s3_client.list_objects_v2.call_count == 3
        calls = mock_s3_client.list_objects_v2.call_args_list
        assert 'ContinuationToken' not in calls[0].kwargs
        assert calls[1].kwargs['ContinuationToken'] == 'token-2'
        assert calls[2].kwargs['ContinuationToken'] == 'token-3'

    def test_search_paths_stops_when_truncated_without_token(
        self, service: AwsEventService, mock_s3_client
    ):
        """Test that a truncated response with no continuation token terminates."""
        mock_s3_client.list_objects_v2.return_value = {
            'Contents': [{'Key': 'event1.json'}],
            'IsTruncated': True,
        }

        result = service._search_paths(Path('prefix'))

        assert result == [Path('event1.json')]
        mock_s3_client.list_objects_v2.assert_called_once()


class TestAwsEventServiceIntegration:
    """Integration tests for AwsEventService."""

    @pytest.mark.asyncio
    async def test_get_conversation_path_with_user_id(self, service: AwsEventService):
        """Test conversation path generation with user_id."""
        conversation_id = uuid4()

        path = await service.get_conversation_path(conversation_id)

        assert 'users' in str(path)
        assert 'test_user' in str(path)
        assert 'v1_conversations' in str(path)
        assert conversation_id.hex in str(path)

    @pytest.mark.asyncio
    async def test_get_conversation_path_without_user_id(
        self, service_no_user: AwsEventService
    ):
        """Test conversation path generation without user_id."""
        conversation_id = uuid4()

        path = await service_no_user.get_conversation_path(conversation_id)

        assert 'users' in str(path)
        assert 'test_user' not in str(path)
        assert 'v1_conversations' in str(path)
        assert conversation_id.hex in str(path)


class TestAwsEventServiceInjector:
    """Test cases for AwsEventServiceInjector."""

    def test_injector_has_bucket_name(self):
        """Test that injector has bucket_name attribute."""
        injector = AwsEventServiceInjector(bucket_name='my-bucket')
        assert injector.bucket_name == 'my-bucket'

    def test_injector_has_default_prefix(self):
        """Test that injector has default prefix."""
        injector = AwsEventServiceInjector(bucket_name='my-bucket')
        assert injector.prefix == Path('users')


class TestGetSharedS3Client:
    """Test cases for the process-wide shared S3 client factory."""

    def setup_method(self):
        aws_event_service._get_shared_s3_client.cache_clear()

    def teardown_method(self):
        aws_event_service._get_shared_s3_client.cache_clear()

    def test_reuses_client_for_same_endpoint(self):
        """Test that repeated calls share one client per endpoint."""
        with patch(
            'openhands.app_server.event.aws_event_service.boto3.client'
        ) as mock_boto3_client:
            mock_boto3_client.side_effect = [MagicMock(), MagicMock()]

            first = aws_event_service._get_shared_s3_client('https://s3.example.com')
            second = aws_event_service._get_shared_s3_client('https://s3.example.com')
            other = aws_event_service._get_shared_s3_client(None)

        assert first is second
        assert first is not other
        assert mock_boto3_client.call_count == 2

    def test_creates_client_with_expanded_connection_pool(self):
        """Test that the shared client is configured with a larger urllib3 pool."""
        with patch(
            'openhands.app_server.event.aws_event_service.boto3.client'
        ) as mock_boto3_client:
            aws_event_service._get_shared_s3_client('https://s3.example.com')

        config = mock_boto3_client.call_args.kwargs['config']
        assert config.max_pool_connections == aws_event_service._S3_MAX_POOL_CONNECTIONS


class TestGetDefaultAwsEndpointUrl:
    """Test cases for _get_default_aws_endpoint_url function."""

    def test_no_env_vars_returns_none(self, monkeypatch):
        """Test that function returns None when no env vars are set."""
        monkeypatch.delenv('AWS_S3_ENDPOINT', raising=False)
        monkeypatch.delenv('AWS_S3_SECURE', raising=False)

        # Need to reload to get fresh default factory
        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result is None

    def test_endpoint_with_https_prefix_secure(self, monkeypatch):
        """Test endpoint with https:// prefix when secure=true."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'https://minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'true')

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'https://minio.example.com:9000'

    def test_endpoint_without_https_prefix_secure(self, monkeypatch):
        """Test endpoint without https:// prefix when secure=true adds it."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'true')

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'https://minio.example.com:9000'

    def test_endpoint_with_http_prefix_insecure(self, monkeypatch):
        """Test endpoint with http:// prefix when secure=false."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'http://minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'false')

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'http://minio.example.com:9000'

    def test_endpoint_without_http_prefix_insecure(self, monkeypatch):
        """Test endpoint without http:// prefix when secure=false adds it."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'false')

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'http://minio.example.com:9000'

    def test_endpoint_with_http_converted_to_https(self, monkeypatch):
        """Test http:// is converted to https:// when secure=true."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'http://minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'true')

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'https://minio.example.com:9000'

    def test_endpoint_with_https_converted_to_http(self, monkeypatch):
        """Test https:// is converted to http:// when secure=false."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'https://minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'false')

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'http://minio.example.com:9000'

    def test_secure_default_is_true(self, monkeypatch):
        """Test that secure defaults to true when not set."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'minio.example.com:9000')
        monkeypatch.delenv('AWS_S3_SECURE', raising=False)

        importlib.reload(aws_event_service)

        result = aws_event_service._get_default_aws_endpoint_url()
        assert result == 'https://minio.example.com:9000'


class TestAwsEventServiceInjectorEndpointUrl:
    """Test cases for AwsEventServiceInjector endpoint_url field."""

    def test_injector_endpoint_url_from_env(self, monkeypatch):
        """Test that endpoint_url is populated from environment variables."""
        monkeypatch.setenv('AWS_S3_ENDPOINT', 'minio.example.com:9000')
        monkeypatch.setenv('AWS_S3_SECURE', 'false')

        importlib.reload(aws_event_service)

        injector = aws_event_service.AwsEventServiceInjector(bucket_name='my-bucket')
        assert injector.endpoint_url == 'http://minio.example.com:9000'

    def test_injector_accepts_custom_endpoint_url(self, monkeypatch):
        """Test that injector accepts custom endpoint_url parameter."""
        monkeypatch.delenv('AWS_S3_ENDPOINT', raising=False)
        monkeypatch.delenv('AWS_S3_SECURE', raising=False)

        importlib.reload(aws_event_service)

        injector = aws_event_service.AwsEventServiceInjector(
            bucket_name='my-bucket', endpoint_url='https://custom.example.com:9000'
        )
        assert injector.endpoint_url == 'https://custom.example.com:9000'

    def test_injector_endpoint_url_none_when_no_env(self, monkeypatch):
        """Test that endpoint_url is None when no env vars set."""
        monkeypatch.delenv('AWS_S3_ENDPOINT', raising=False)
        monkeypatch.delenv('AWS_S3_SECURE', raising=False)

        importlib.reload(aws_event_service)

        injector = aws_event_service.AwsEventServiceInjector(bucket_name='my-bucket')
        assert injector.endpoint_url is None


def _make_s3_client_with_store() -> tuple[MagicMock, dict[str, bytes]]:
    """Return a mock S3 client backed by an in-memory {key: bytes} store.

    Supports the operations used by AwsEventService: get_object, put_object,
    head_object, list_objects_v2, copy_object, delete_object.
    """
    store: dict[str, bytes] = {}

    def get_object(Bucket, Key):
        if Key not in store:
            err = {'Error': {'Code': 'NoSuchKey'}}
            raise botocore.exceptions.ClientError(err, 'GetObject')
        body = MagicMock()
        body.__enter__ = MagicMock(return_value=body)
        body.__exit__ = MagicMock(return_value=False)
        body.read.return_value = store[Key]
        return {'Body': body}

    def put_object(Bucket, Key, Body):
        store[Key] = Body if isinstance(Body, bytes) else Body.encode('utf-8')

    def head_object(Bucket, Key):
        if Key not in store:
            err = {'Error': {'Code': '404'}}
            raise botocore.exceptions.ClientError(err, 'HeadObject')
        return {}

    def list_objects_v2(Bucket, Prefix, **kwargs):
        contents = [{'Key': k} for k in sorted(store) if k.startswith(str(Prefix))]
        return {'Contents': contents, 'IsTruncated': False}

    def copy_object(Bucket, CopySource, Key):
        src = CopySource['Key']
        store[Key] = store[src]

    def delete_object(Bucket, Key):
        store.pop(Key, None)

    client = MagicMock()
    client.get_object.side_effect = get_object
    client.put_object.side_effect = put_object
    client.head_object.side_effect = head_object
    client.list_objects_v2.side_effect = list_objects_v2
    client.copy_object.side_effect = copy_object
    client.delete_object.side_effect = delete_object
    return client, store


class TestAwsEventServiceIndex:
    """Tests for the S3 index primitives and end-to-end index behavior."""

    @pytest.fixture
    def store_service(self):
        client, store = _make_s3_client_with_store()
        svc = AwsEventService(
            prefix=Path('users'),
            user_id='test_user',
            app_conversation_info_service=None,
            s3_client=client,
            bucket_name='test-bucket',
            app_conversation_info_load_tasks={},
        )
        return svc, store

    @pytest.mark.asyncio
    async def test_index_store_and_load(self, store_service):
        svc, store = store_service
        conversation_path = await svc.get_conversation_path(uuid4())
        index_path = svc._index_path(conversation_path)
        index = [['id1', '2025-01-01T00:00:00', 'TokenEvent']]

        svc._store_index(index_path, index)
        assert str(index_path) in store

        loaded = svc._load_index(index_path)
        assert loaded == index

    def test_index_load_missing_returns_none(self, store_service):
        svc, _ = store_service
        path = Path('users/test_user/v1_conversations/abc/index.json')
        assert svc._load_index(path) is None

    def test_index_exists_true_false(self, store_service):
        svc, store = store_service
        path = Path('users/test_user/v1_conversations/abc/index.json')
        assert not svc._index_exists(path)
        store[str(path)] = b'[]'
        assert svc._index_exists(path)

    @pytest.mark.asyncio
    async def test_invalidate_renames_index_to_stale(self, store_service):
        svc, store = store_service
        conversation_path = await svc.get_conversation_path(uuid4())
        index_path = svc._index_path(conversation_path)
        stale_path = svc._index_stale_path(conversation_path)
        store[str(index_path)] = b'[]'

        svc._invalidate_index(conversation_path)
        assert str(index_path) not in store
        assert str(stale_path) in store

    @pytest.mark.asyncio
    async def test_invalidate_idempotent_when_no_index(self, store_service):
        svc, store = store_service
        conversation_path = await svc.get_conversation_path(uuid4())
        # No index.json present -> no-op, no error.
        svc._invalidate_index(conversation_path)
        assert svc._index_stale_path(conversation_path) not in store

    @pytest.mark.asyncio
    async def test_search_builds_and_uses_index(self, store_service):
        svc, _ = store_service
        conversation_id = uuid4()
        for _ in range(3):
            await svc.save_event(conversation_id, create_token_event())

        result = await svc.search_events(conversation_id)
        assert len(result.items) == 3

        # index.json should now exist in the store.
        conversation_path = await svc.get_conversation_path(conversation_id)
        index_path = svc._index_path(conversation_path)
        assert svc._index_exists(index_path)

        # Second search uses the index without rebuilding.
        result2 = await svc.search_events(conversation_id)
        assert len(result2.items) == 3

    @pytest.mark.asyncio
    async def test_save_invalidates_then_search_rebuilds(self, store_service):
        svc, _ = store_service
        conversation_id = uuid4()
        await svc.save_event(conversation_id, create_token_event())
        await svc.search_events(conversation_id)  # build index

        conversation_path = await svc.get_conversation_path(conversation_id)
        index_path = svc._index_path(conversation_path)
        stale_path = svc._index_stale_path(conversation_path)
        assert svc._index_exists(index_path)

        await svc.save_event(conversation_id, create_token_event())
        assert not svc._index_exists(index_path)
        assert svc._index_exists(stale_path)

        # Search rebuilds from stale + scans the new event.
        result = await svc.search_events(conversation_id)
        assert len(result.items) == 2
        assert svc._index_exists(index_path)

    @pytest.mark.asyncio
    async def test_malformed_index_rebuilds(self, store_service):
        svc, store = store_service
        conversation_id = uuid4()
        await svc.save_event(conversation_id, create_token_event())
        await svc.search_events(conversation_id)
        conversation_path = await svc.get_conversation_path(conversation_id)
        index_path = svc._index_path(conversation_path)
        store[str(index_path)] = b'{not json'

        result = await svc.search_events(conversation_id)
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_filtered_count_via_index(self, store_service):
        svc, _ = store_service
        conversation_id = uuid4()
        for _ in range(3):
            await svc.save_event(conversation_id, create_token_event())
        await svc.save_event(conversation_id, create_pause_event())
        await svc.search_events(conversation_id)  # build index

        count = await svc.count_events(conversation_id, kind__eq='TokenEvent')
        assert count == 3
        count_pause = await svc.count_events(conversation_id, kind__eq='PauseEvent')
        assert count_pause == 1
