"""The linking hint is optional routing metadata, never a login or token grant."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from keycloak.exceptions import (
    KeycloakAuthenticationError,
    KeycloakConnectionError,
    KeycloakGetError,
)
from pydantic import SecretStr, ValidationError

from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import KeycloakUserInfo
from server.routes.auth import api_router


@pytest.fixture(autouse=True)
def legacy_session():
    user_auth = SaasUserAuth(user_id='user', refresh_token=SecretStr('test-refresh'))
    with patch('server.routes.auth.get_user_auth', AsyncMock(return_value=user_auth)):
        yield user_auth


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api_router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    'marker, expected',
    [
        ('enterprise_sso:saml', 'enterprise_sso'),
        ('enterprise_sso:oidc', 'enterprise_sso'),
        ('github', None),
        ('gitlab', None),
        ('azure_devops', None),
        ('forgejo', None),
        ('unknown', None),
        (None, None),
    ],
)
def test_authenticated_hint_has_no_tokens_or_session_cookie(client, marker, expected):
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('access-token')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(
                return_value=KeycloakUserInfo(sub='user', identity_provider=marker)
            ),
        ) as userinfo,
    ):
        response = client.post('/api/keycloak/link-context')
    assert response.status_code == 200
    assert response.json() == {'provider': expected}
    assert 'set-cookie' not in response.headers
    assert 'access-token' not in response.text
    userinfo.assert_awaited_once_with('access-token')


@pytest.mark.parametrize('session', ['native', 'bearer'])
def test_non_keycloak_credentials_are_never_resolved_or_forwarded(
    client, legacy_session, session
):
    if session == 'native':
        legacy_session.oauth_v2_cookie = True
    else:
        legacy_session.auth_type = AuthType.BEARER
    with (
        patch('server.routes.auth.get_access_token', AsyncMock()) as token,
        patch(
            'server.routes.auth.token_manager.get_user_info', AsyncMock()
        ) as userinfo,
    ):
        response = client.post('/api/keycloak/link-context')
    assert response.status_code == 200
    assert response.json() == {'provider': None}
    token.assert_not_called()
    userinfo.assert_not_called()


def test_unauthenticated_request_never_fetches_userinfo(client):
    with (
        patch('server.routes.auth.get_access_token', AsyncMock(return_value=None)),
        patch(
            'server.routes.auth.token_manager.get_user_info', AsyncMock()
        ) as userinfo,
    ):
        response = client.post('/api/keycloak/link-context')
    assert response.status_code == 401
    assert 'set-cookie' not in response.headers
    userinfo.assert_not_called()


@pytest.mark.parametrize(
    'code, expected', [(401, 401), (403, 401), (429, 503), (500, 503), (503, 503)]
)
def test_keycloak_http_errors_are_sanitized(client, code, expected):
    error = KeycloakGetError(
        error_message='private upstream detail', response_code=code
    )
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('access-token')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(side_effect=error),
        ),
    ):
        response = client.post('/api/keycloak/link-context')
    assert response.status_code == expected
    assert 'private upstream detail' not in response.text
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize(
    'error, expected',
    [(KeycloakConnectionError(), 503), (KeycloakAuthenticationError(), 401)],
)
def test_keycloak_connection_and_authentication_failures(client, error, expected):
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('access-token')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(side_effect=error),
        ),
    ):
        response = client.post('/api/keycloak/link-context')
    assert response.status_code == expected
    assert 'set-cookie' not in response.headers


def test_invalid_userinfo_cannot_supply_a_hint(client):
    try:
        KeycloakUserInfo.model_validate({})
    except ValidationError as exc:
        error = exc
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('access-token')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(side_effect=error),
        ),
    ):
        response = client.post('/api/keycloak/link-context')
    assert response.status_code == 401
    assert 'set-cookie' not in response.headers
