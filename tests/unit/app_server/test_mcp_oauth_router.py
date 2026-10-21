"""Unit tests for the ``/api/v1/mcp/oauth/*`` routes.

Redis, the JWT service and the SDK probe are replaced with in-memory fakes:
the tests cover the routes' own behaviour — driving FastMCP's OAuth handshake
through the app server's public callback, scoping jobs to their owner, and the
page the provider's redirect lands on.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from ipaddress import ip_address
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyUrl

from openhands.agent_server.mcp_router import MCPOAuthStateResponse, MCPTestSuccess
from openhands.app_server.mcp import mcp_oauth_router
from openhands.app_server.mcp.mcp_oauth_router import (
    _CloudCoordinatedOAuth,
    callback_router,
    router,
)
from openhands.app_server.user_auth import get_user_id, get_user_settings
from openhands.app_server.utils.dependencies import check_session_api_key

MCP_URL = 'https://mcp.atlassian.example/v1/mcp'
WEB_URL = 'https://app.example.test'
CALLBACK_URL = f'{WEB_URL}/api/v1/mcp/oauth/callback'
USER_ID = 'user-1'
OAUTH_SERVER = {
    'url': MCP_URL,
    'auth': {
        'strategy': 'oauth2',
        'authentication': {'type': 'oauth', 'client_auth_method': 'none'},
    },
}


class _FakeRedis:
    """In-memory stand-in for the sync client; returns bytes like redis-py."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        return True

    def get(self, key):
        value = self.strings.get(key)
        return value.encode() if value is not None else None

    def delete(self, key):
        return int(self.strings.pop(key, None) is not None)

    def hset(self, key, field=None, value=None, mapping=None):
        entry = self.hashes.setdefault(key, {})
        if mapping:
            entry.update({str(k): str(v) for k, v in mapping.items()})
        if field is not None:
            entry[str(field)] = str(value)
        return 1

    def hmget(self, key, fields):
        entry = self.hashes.get(key, {})
        return [entry[f].encode() if f in entry else None for f in fields]

    def hgetall(self, key):
        return {k.encode(): v.encode() for k, v in self.hashes.get(key, {}).items()}

    def expire(self, key, ttl):
        return True


class _FakeAsyncRedis:
    """Async facade over ``_FakeRedis`` for the route-side client."""

    def __init__(self, sync: _FakeRedis) -> None:
        self._sync = sync

    def __getattr__(self, name: str):
        method = getattr(self._sync, name)

        async def call(*args, **kwargs):
            return method(*args, **kwargs)

        return call


@pytest.fixture
def redis():
    fake = _FakeRedis()
    with (
        patch.object(mcp_oauth_router, 'get_redis_client', return_value=fake),
        patch.object(
            mcp_oauth_router,
            'get_redis_client_async',
            return_value=_FakeAsyncRedis(fake),
        ),
    ):
        yield fake


@pytest.fixture(autouse=True)
def app_config():
    """Public web URL plus a JWT service whose encryption is recognisable."""
    jwt = SimpleNamespace(
        encrypt_value=lambda value: f'enc:{value}',
        decrypt_value=lambda value: value.removeprefix('enc:'),
    )
    config = SimpleNamespace(
        web_url=WEB_URL, jwt=SimpleNamespace(get_jwt_service=lambda: jwt)
    )
    with patch.object(mcp_oauth_router, 'get_global_config', return_value=config):
        yield config


@pytest.fixture(autouse=True)
def resolve():
    """Let the shared SSRF guard treat the MCP host as public."""
    with patch(
        'openhands.app_server.mcp.mcp_test_router._resolve_probe_addresses'
    ) as mock:
        mock.return_value = [ip_address('203.0.113.10')]
        yield mock


def _client(user_id: str | None = USER_ID) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.include_router(callback_router, prefix='/api/v1')
    app.dependency_overrides[check_session_api_key] = lambda: None
    app.dependency_overrides[get_user_settings] = lambda: None
    app.dependency_overrides[get_user_id] = lambda: user_id
    return TestClient(app, raise_server_exceptions=False)


def _wait_for(read: Callable[[], Any], done: Callable[[Any], bool], timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if done(value) or time.monotonic() > deadline:
            return value
        time.sleep(0.05)


def _handshake_probe(seen: dict[str, Any]):
    """Stand in for the SDK probe: run the OAuth handlers, then succeed."""

    def probe(request, cipher, mcp_oauth_factory=None):
        server = request.resolved_server
        oauth = mcp_oauth_factory('server', server, server.oauth_auth, None)

        async def handshake():
            await oauth.redirect_handler(
                'https://auth.example/authorize?client_id=c&state=state-123'
            )
            return await oauth.callback_handler()

        seen['callback'] = asyncio.run(handshake())
        return MCPTestSuccess(
            tools=['read_subject'],
            oauth_state=MCPOAuthStateResponse(
                tokens={'access_token': 'plain-access-token'}
            ),
        )

    return probe


def test_oauth_install_round_trips_through_the_public_callback(redis):
    # Arrange
    seen: dict[str, Any] = {}
    client = _client()
    with patch.object(
        mcp_oauth_router, '_probe_mcp_server', side_effect=_handshake_probe(seen)
    ):
        # Act: start publishes the authorization URL ...
        start = client.post(
            '/api/v1/mcp/oauth/start',
            json={'name': 'atlassian', 'server': OAUTH_SERVER, 'timeout': 120},
        )
        job_id = start.json()['job_id']
        status_url = f'/api/v1/mcp/oauth/status/{job_id}'
        waiting = _wait_for(
            lambda: client.get(status_url).json(), lambda s: s['callback_ready']
        )
        # ... the provider redirects the browser to the app server ...
        callback = client.get(
            '/api/v1/mcp/oauth/callback',
            params={'code': 'the-code', 'state': 'state-123'},
        )
        # ... and the probe finishes with the tokens.
        final = _wait_for(
            lambda: client.get(status_url).json(),
            lambda s: s['status'] in ('succeeded', 'failed'),
        )
        reused = client.get(
            '/api/v1/mcp/oauth/callback',
            params={'code': 'the-code', 'state': 'state-123'},
        )

    # Assert
    assert start.status_code == 200
    assert start.json() == {
        'ok': True,
        'job_id': job_id,
        'authorization_url': (
            'https://auth.example/authorize?client_id=c&state=state-123'
        ),
    }
    assert waiting['status'] == 'authorizing'
    assert callback.status_code == 200
    assert 'close this window' in callback.text
    assert seen['callback'] == ('the-code', 'state-123')
    assert final['status'] == 'succeeded'
    assert final['tools'] == ['read_subject']
    assert final['oauth_state']['tokens']['access_token'] == 'plain-access-token'
    assert redis.hashes[f'mcp_oauth:job:{job_id}']['result'].startswith('enc:')
    assert reused.status_code == 404


def test_start_succeeds_without_consent_when_stored_tokens_work(redis):
    # Arrange: the probe connects with the restored tokens (still valid, or
    # refreshed) and never asks the user for consent.
    def probe(request, cipher, mcp_oauth_factory=None):
        return MCPTestSuccess(
            tools=['read_subject'],
            oauth_state=MCPOAuthStateResponse(
                tokens={'access_token': 'refreshed-access-token'}
            ),
        )

    client = _client()
    with patch.object(mcp_oauth_router, '_probe_mcp_server', side_effect=probe):
        # Act
        start = client.post(
            '/api/v1/mcp/oauth/start',
            json={'name': 'atlassian', 'server': OAUTH_SERVER, 'timeout': 120},
        )
        job_id = start.json()['job_id']
        final = _wait_for(
            lambda: client.get(f'/api/v1/mcp/oauth/status/{job_id}').json(),
            lambda s: s['status'] in ('succeeded', 'failed'),
        )

    # Assert: success with only the job id; the state is on the status route.
    assert start.status_code == 200
    assert start.json() == {'ok': True, 'job_id': job_id}
    assert final['status'] == 'succeeded'
    assert final['oauth_state']['tokens']['access_token'] == 'refreshed-access-token'


def test_start_requires_an_oauth_credential(redis):
    client = _client()

    response = client.post('/api/v1/mcp/oauth/start', json={'server': {'url': MCP_URL}})

    assert response.status_code == 400
    assert redis.hashes == {}


def test_status_hides_jobs_of_other_users(redis):
    job_id = 'a' * 32
    redis.hashes[f'mcp_oauth:job:{job_id}'] = {
        'user_id': 'someone-else',
        'status': 'pending',
        'callback_ready': '0',
    }
    client = _client()

    foreign = client.get(f'/api/v1/mcp/oauth/status/{job_id}')
    malformed = client.get('/api/v1/mcp/oauth/status/not-a-job')

    assert foreign.status_code == 404
    assert malformed.status_code == 404


def test_callback_records_a_provider_error_on_the_job(redis):
    job_id = 'b' * 32
    redis.hashes[f'mcp_oauth:job:{job_id}'] = {
        'user_id': USER_ID,
        'status': 'authorizing',
        'callback_ready': '1',
    }
    redis.strings['mcp_oauth:state:state-err'] = job_id
    client = _client()

    response = client.get(
        '/api/v1/mcp/oauth/callback',
        params={
            'state': 'state-err',
            'error': 'access_denied',
            'error_description': 'User declined',
        },
    )

    assert response.status_code == 200
    assert 'declined' in response.text
    assert (
        redis.hashes[f'mcp_oauth:job:{job_id}']['callback_error']
        == 'access_denied: User declined'
    )
    assert 'mcp_oauth:state:state-err' not in redis.strings


def test_callback_rejects_unknown_state(redis):
    client = _client()

    response = client.get(
        '/api/v1/mcp/oauth/callback', params={'code': 'x', 'state': 'never-issued'}
    )

    assert response.status_code == 404


@pytest.mark.filterwarnings('ignore:Using in-memory token storage')
def test_cloud_oauth_client_registers_the_public_callback():
    oauth = _CloudCoordinatedOAuth(
        job_id='job',
        redirect_uri=CALLBACK_URL,
        authorization_ready=threading.Event(),
        mcp_url=MCP_URL,
    )

    assert oauth.context.client_metadata.redirect_uris == [AnyUrl(CALLBACK_URL)]
