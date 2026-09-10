"""Provider HTTP outages must not invalidate an established browser login."""

import time
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import Response
from keycloak.exceptions import (
    KeycloakAuthenticationError,
    KeycloakGetError,
    KeycloakPostError,
)
from tenacity import Future, RetryError

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.types import SessionExpiredError
from server.auth import mode
from server.auth.authentication import AuthenticationService
from server.auth.contracts import AuthenticationUnavailable, InvalidCredentials
from server.auth.keycloak.errors import is_transient_keycloak_error
from server.auth.keycloak.token_manager import TokenManager
from server.middleware import SetAuthCookieMiddleware
from storage.encrypt_utils import get_jwt_service
from tests.unit.server.auth.test_shared_authentication import (
    auth_database as auth_database,
)
from tests.unit.server.auth.test_shared_authentication import request

TRANSIENT_STATUSES = [408, 429, 500, 502, 503, 504]
PRIVATE_UPSTREAM_MESSAGE = 'invalid_grant upstream-credential-value'


def retry_error(error):
    future = Future(1)
    future.set_exception(error)
    return RetryError(future)


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    client = MagicMock()
    monkeypatch.setattr(
        'server.auth.keycloak.token_manager.get_keycloak_openid', lambda *_: client
    )
    tokens = TokenManager()
    tokens.load_offline_token = AsyncMock(return_value='offline-token')
    return tokens, client


@pytest.mark.parametrize('status', [400, 401, 403, 408, 429, 500, 502, 503, 504])
@pytest.mark.parametrize('error_type', [KeycloakGetError, KeycloakPostError])
def test_http_status_classification_survives_retry_wrappers(status, error_type):
    error = error_type(PRIVATE_UPSTREAM_MESSAGE, response_code=status)
    expected = status in TRANSIENT_STATUSES
    assert is_transient_keycloak_error(error) is expected
    assert is_transient_keycloak_error(retry_error(retry_error(error))) is expected


@pytest.mark.parametrize('status', TRANSIENT_STATUSES)
@pytest.mark.parametrize(
    'operation,sdk_method,error_type',
    [
        ('exchange', 'a_token', KeycloakPostError),
        ('userinfo', 'a_userinfo', KeycloakGetError),
        ('refresh', 'a_refresh_token', KeycloakPostError),
        ('validate_offline', 'a_refresh_token', KeycloakPostError),
        ('introspect', 'a_introspect', KeycloakPostError),
        ('offline_provider', 'a_refresh_token', KeycloakPostError),
    ],
)
async def test_http_outage_propagates_without_payload_logging(
    upstream, caplog, status, operation, sdk_method, error_type
):
    tokens, client = upstream
    setattr(
        client,
        sdk_method,
        AsyncMock(
            side_effect=error_type(PRIVATE_UPSTREAM_MESSAGE, response_code=status)
        ),
    )
    with pytest.raises(AuthenticationUnavailable):
        await invoke(tokens, operation)
    assert PRIVATE_UPSTREAM_MESSAGE not in caplog.text
    assert mode.get_auth_mode() is mode.AuthMode.KEYCLOAK


async def invoke(tokens, operation):
    if operation == 'exchange':
        return await tokens.get_keycloak_tokens('code', 'https://app.example/callback')
    if operation == 'userinfo':
        return await tokens.get_user_info('access-token')
    if operation == 'refresh':
        return await tokens.refresh('refresh-token')
    if operation == 'validate_offline':
        return await tokens.validate_offline_token('user-id')
    if operation == 'introspect':
        return await tokens.check_offline_token_is_active('user-id')
    return await tokens.get_idp_token_from_offline_token('offline', ProviderType.GITHUB)


async def test_wrapped_token_exchange_outage_is_not_swallowed(upstream):
    tokens, client = upstream
    client.a_token = AsyncMock(
        side_effect=retry_error(KeycloakPostError('temporarily unavailable', 503))
    )
    with pytest.raises(AuthenticationUnavailable):
        await invoke(tokens, 'exchange')


@pytest.mark.parametrize('status', [400, 401, 403])
async def test_invalid_credentials_keep_legacy_exchange_and_offline_shapes(
    upstream, status
):
    tokens, client = upstream
    client.a_token = AsyncMock(side_effect=KeycloakPostError('invalid_grant', status))
    client.a_userinfo = AsyncMock(side_effect=KeycloakGetError('invalid_grant', status))
    client.a_refresh_token = AsyncMock(
        side_effect=KeycloakPostError('invalid_grant', status)
    )
    client.a_introspect = AsyncMock(
        side_effect=KeycloakPostError('invalid_grant', status)
    )
    assert await invoke(tokens, 'exchange') == (None, None)
    assert await invoke(tokens, 'validate_offline') is False
    assert await invoke(tokens, 'introspect') is False
    for operation in ('refresh', 'userinfo'):
        with pytest.raises(InvalidCredentials):
            await invoke(tokens, operation)
    with pytest.raises(SessionExpiredError):
        await invoke(tokens, 'offline_provider')


async def test_userinfo_http_outage_does_not_attempt_credential_refresh(upstream):
    tokens, client = upstream
    client.a_userinfo = AsyncMock(
        side_effect=KeycloakAuthenticationError('failure', 503)
    )
    client.a_refresh_token = AsyncMock()
    with pytest.raises(AuthenticationUnavailable):
        await tokens.verify_keycloak_token('access-token', 'refresh-token')
    client.a_refresh_token.assert_not_awaited()


async def test_duplicate_policy_outage_is_not_an_empty_identity_result(
    upstream, monkeypatch
):
    tokens, client = upstream
    monkeypatch.setattr(
        'server.auth.keycloak.token_manager.DUPLICATE_EMAIL_CHECK', True
    )
    monkeypatch.setattr(
        'server.auth.keycloak.token_manager.get_keycloak_admin', lambda *_: client
    )
    client.a_get_users = AsyncMock(side_effect=KeycloakGetError('failure', 503))
    with pytest.raises(AuthenticationUnavailable):
        await tokens.check_duplicate_base_email('person+tag@example.com', 'user-id')
    client.a_get_users.assert_awaited_once()


@pytest.mark.parametrize('status', TRANSIENT_STATUSES)
async def test_cookie_refresh_http_outage_preserves_cookie_and_mode(
    auth_database,  # noqa: F811
    upstream,
    monkeypatch,
    status,
):
    _, user, _ = auth_database
    _, client = upstream
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    client.a_refresh_token = AsyncMock(
        side_effect=KeycloakPostError(PRIVATE_UPSTREAM_MESSAGE, response_code=status)
    )
    service = get_jwt_service()
    cookie = service.create_jws_token(
        {
            'access_token': jwt.encode(
                {'sub': str(user.id), 'exp': int(time.time()) - 1},
                'synthetic-provider-signing-key-at-least-32-bytes',
            ),
            'refresh_token': 'unchanged-refresh-token',
            'accepted_tos': True,
        }
    )
    monkeypatch.setattr(
        'server.middleware.get_user_auth', AuthenticationService().authenticate_request
    )
    req = request(cookies={'keycloak_auth': cookie})
    downstream = AsyncMock(return_value=Response(status_code=200))
    response = await SetAuthCookieMiddleware()(req, downstream)
    assert response.status_code == 503
    assert 'set-cookie' not in response.headers
    assert req.cookies['keycloak_auth'] == cookie
    assert mode.get_auth_mode() is mode.AuthMode.KEYCLOAK
    assert PRIVATE_UPSTREAM_MESSAGE.encode() not in response.body
    downstream.assert_not_awaited()
