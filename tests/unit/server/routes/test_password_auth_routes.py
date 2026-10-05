"""Route tests for ``/api/auth/password/*``.

These cover what the routes own rather than what the service owns: the feature
gate, the session cookie, the error mapping and the rate limits. Redis is a
small in-memory stand-in with the same ``set(nx=, ex=)`` semantics, so the
limiter under test is the real one. ``PasswordAuthService`` is stubbed where a
test only cares about the route around it; its own behaviour is covered against
a real database in ``tests/unit/server/services/test_password_auth_service.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.password_auth import PasswordAuthError
from server.routes import password_auth


class FakeRedis:
    """Just enough Redis for the limiter: SET with NX and an expiry."""

    def __init__(self) -> None:
        self.keys: dict[str, int] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return False
        self.keys[key] = ex
        return True


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def client(redis):
    application = FastAPI()
    application.include_router(password_auth.password_auth_router)
    jwt_service = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test-secret'), active=True)]
    )
    with (
        patch(
            'server.utils.rate_limit_utils.get_redis_client_async', return_value=redis
        ),
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_service),
    ):
        yield TestClient(application)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('ENABLE_PASSWORD_AUTH', 'true')
    with patch('server.auth.password_auth.DEPLOYMENT_MODE', 'self_hosted'):
        yield


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setenv('ENABLE_PASSWORD_AUTH', 'false')
    yield


def test_status_reports_the_feature_gate(client, enabled):
    body = client.get('/api/auth/password/status').json()

    assert body['enabled'] is True
    assert body['minimum_password_length'] == 8


def test_status_reports_disabled_on_saas(client, disabled):
    assert client.get('/api/auth/password/status').json()['enabled'] is False


def test_login_is_404_when_the_feature_is_off(client, disabled):
    response = client.post(
        '/api/auth/password/login',
        json={'email': 'someone@example.com', 'password': 'a-password'},
    )

    assert response.status_code == 404


def test_login_sets_a_session_cookie(client, enabled):
    user_id = uuid4()
    with patch.object(
        password_auth.PasswordAuthService,
        'login',
        new_callable=AsyncMock,
        return_value=(user_id, 7),
    ):
        response = client.post(
            '/api/auth/password/login',
            json={'email': 'someone@example.com', 'password': 'a-password'},
        )

    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert cookie.startswith('openhands_auth=')
    assert 'HttpOnly' in cookie


def test_login_maps_the_service_error(client, enabled):
    with patch.object(
        password_auth.PasswordAuthService,
        'login',
        new_callable=AsyncMock,
        side_effect=PasswordAuthError(
            'Invalid email or password', 401, 'invalid_credentials'
        ),
    ):
        response = client.post(
            '/api/auth/password/login',
            json={'email': 'someone@example.com', 'password': 'a-password'},
        )

    assert response.status_code == 401
    assert response.json()['detail']['code'] == 'invalid_credentials'
    assert 'set-cookie' not in response.headers


def test_login_is_rate_limited_per_ip_across_accounts(client, enabled):
    """Spreading guesses over many accounts must not dodge the limit."""
    with patch.object(
        password_auth.PasswordAuthService,
        'login',
        new_callable=AsyncMock,
        side_effect=PasswordAuthError('Invalid email or password', 401),
    ):
        first = client.post(
            '/api/auth/password/login',
            json={'email': 'one@example.com', 'password': 'a-password'},
        )
        second = client.post(
            '/api/auth/password/login',
            json={'email': 'two@example.com', 'password': 'a-password'},
        )

    assert first.status_code == 401
    assert second.status_code == 429


def test_login_is_rate_limited_per_account(client, enabled, redis):
    """The per-account key is separate, so a shared IP cannot be used up."""
    with patch.object(
        password_auth.PasswordAuthService,
        'login',
        new_callable=AsyncMock,
        side_effect=PasswordAuthError('Invalid email or password', 401),
    ):
        client.post(
            '/api/auth/password/login',
            json={'email': 'one@example.com', 'password': 'a-password'},
        )

    account_keys = [k for k in redis.keys if k.startswith('password_login:')]
    assert any(':by_ip:' not in key for key in account_keys)
    assert any(key.startswith('password_login:by_ip:ip:') for key in redis.keys)


@pytest.mark.parametrize(
    'path,payload',
    [
        ('/api/auth/password/inspect', {'token': 'a-token'}),
        (
            '/api/auth/password/complete',
            {'token': 'a-token', 'password': 'a-password'},
        ),
    ],
)
def test_token_endpoints_are_rate_limited_per_ip(client, enabled, path, payload):
    with (
        patch.object(
            password_auth.PasswordAuthService,
            'inspect_token',
            new_callable=AsyncMock,
            side_effect=PasswordAuthError('This password link is invalid', 400),
        ),
        patch.object(
            password_auth.PasswordAuthService,
            'complete_token',
            new_callable=AsyncMock,
            side_effect=PasswordAuthError('This password link is invalid', 400),
        ),
    ):
        first = client.post(path, json=payload)
        second = client.post(path, json=payload)

    assert first.status_code == 400
    assert second.status_code == 429


def test_oversized_token_is_rejected_before_any_lookup(client, enabled):
    with patch.object(
        password_auth.PasswordAuthService, 'inspect_token', new_callable=AsyncMock
    ) as inspect:
        response = client.post('/api/auth/password/inspect', json={'token': 'x' * 257})

    assert response.status_code == 422
    inspect.assert_not_called()


def test_complete_sets_a_session_cookie(client, enabled):
    with patch.object(
        password_auth.PasswordAuthService,
        'complete_token',
        new_callable=AsyncMock,
        return_value=(uuid4(), 2),
    ):
        response = client.post(
            '/api/auth/password/complete',
            json={'token': 'a-token', 'password': 'a-password'},
        )

    assert response.status_code == 200
    assert response.headers['set-cookie'].startswith('openhands_auth=')
