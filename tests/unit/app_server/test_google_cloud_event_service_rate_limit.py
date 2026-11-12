"""The GCS event service against a fake GCS that rate-limits object changes.

GCS allows about one change per second to one object. fake-gcs-server has no
such limit, so the session below refuses a second change to the same object
within ``WINDOW_SECONDS``, the way GCS answers: a 429.
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
from tenacity import wait_fixed
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from openhands.app_server.event import event_service_base
from openhands.app_server.event.google_cloud_event_service import (
    GoogleCloudEventService,
)
from openhands.sdk.event import TokenEvent

IMAGE = 'fsouza/fake-gcs-server:1.52.2'
BUCKET = 'events'
# Shorter than GCS's one second, so the test stays fast.
WINDOW_SECONDS = 0.2


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def fake_gcs_url() -> Iterator[str]:
    # Resumable uploads continue at the URL the server advertises, so it must
    # be the address the test reaches, known before the container starts.
    port = _free_port()
    url = f'http://localhost:{port}'
    container = (
        DockerContainer(IMAGE)
        .with_bind_ports(4443, port)
        .with_command(f'-scheme http -external-url {url} -backend memory')
    )
    container.start()
    try:
        wait_for_logs(container, 'server started at')
        yield url
    finally:
        container.stop()


def _changed_object(request: requests.PreparedRequest) -> str | None:
    """The object a request changes, or None for a read."""
    url = urlparse(request.url)
    path = unquote(url.path)
    if request.method == 'POST' and path.startswith('/upload/storage/v1/b/'):
        name = parse_qs(url.query).get('name', [None])[0]
        if name is None and request.body:
            name = json.loads(request.body).get('name')
        return name
    if '/copyTo/' in path or '/rewriteTo/' in path:
        return path.split('/o/')[-1]
    if request.method in ('DELETE', 'PATCH') and '/o/' in path:
        return path.split('/o/', 1)[1]
    return None


class RateLimitingAdapter(requests.adapters.HTTPAdapter):
    """Refuses a second change to one object within the window."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._changed_at: dict[str, float] = {}
        self.refused: list[str] = []

    def send(self, request, *args, **kwargs):
        name = _changed_object(request)
        if name is not None:
            with self._lock:
                now = time.monotonic()
                if now - self._changed_at.get(name, -WINDOW_SECONDS) < WINDOW_SECONDS:
                    self.refused.append(name)
                    return self._too_many_requests(request, name)
                self._changed_at[name] = now
        return super().send(request, *args, **kwargs)

    @staticmethod
    def _too_many_requests(request, name: str) -> requests.Response:
        response = requests.Response()
        response.status_code = 429
        response.request = request
        response.url = request.url
        response.headers['Content-Type'] = 'application/json'
        message = (
            f'The object {BUCKET}/{name} exceeded the rate limit for object '
            'mutation operations (create, update, and delete).'
        )
        response._content = json.dumps(
            {'error': {'code': 429, 'message': message}}
        ).encode()
        return response


@pytest.fixture
def rate_limit() -> RateLimitingAdapter:
    return RateLimitingAdapter()


@pytest.fixture
def bucket(fake_gcs_url: str, rate_limit: RateLimitingAdapter) -> storage.Bucket:
    session = requests.Session()
    session.mount('http://', rate_limit)
    client = storage.Client(
        project='test',
        credentials=AnonymousCredentials(),
        client_options={'api_endpoint': fake_gcs_url},
        _http=session,
    )
    bucket = client.bucket(BUCKET)
    if not bucket.exists():
        client.create_bucket(BUCKET)
    return bucket


def _event() -> TokenEvent:
    return TokenEvent(source='agent', prompt_token_ids=[1], response_token_ids=[2])


async def test_a_rate_limited_index_still_finds_every_event(
    bucket: storage.Bucket, rate_limit: RateLimitingAdapter
):
    service = GoogleCloudEventService(
        prefix=Path('users'),
        user_id='test_user',
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
        bucket=bucket,
    )
    conversation_id = uuid4()
    saved = []

    with patch.object(
        event_service_base, '_INVALIDATION_RETRY_WAIT', wait_fixed(WINDOW_SECONDS)
    ):
        # An open conversation page searches while the webhook saves, so the
        # index is rebuilt and invalidated again and again.
        for _ in range(10):
            batch = [_event() for _ in range(3)]
            saved.extend(batch)
            await asyncio.gather(
                service.save_events(conversation_id, batch),
                service.search_events(conversation_id),
            )
        retries = list(event_service_base._invalidation_retries.values())
        await asyncio.wait_for(asyncio.gather(*retries), timeout=30)

    assert rate_limit.refused, 'the fake never refused a change'
    page = await service.search_events(conversation_id, limit=100)
    assert {event.id for event in page.items} == {event.id for event in saved}
