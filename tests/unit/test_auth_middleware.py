"""Account policy runs on the selected principal before endpoint side effects."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import Request, Response
from pydantic import SecretStr

from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.auth_error import AuthError, TokenRefreshError
from server.auth.contracts import (
    AuthenticationUnavailable,
    InvalidCredentials,
    Principal,
    ProviderReconnectRequired,
)
from server.auth.saas_user_auth import SaasUserAuth
from server.middleware import SetAuthCookieMiddleware

USER_ID = UUID('11111111-1111-4111-8111-111111111111')


def request(path='/api/v1/users/me', *, method='GET', cookies=None):
    req = Request(
        {
            'type': 'http',
            'method': method,
            'path': path,
            'scheme': 'https',
            'server': ('app.example.com', 443),
            'headers': [],
            'query_string': b'',
        }
    )
    req._cookies = cookies or {}
    return req


def authenticate(
    req, *, method='keycloak', restricted=False, accepted_tos=True, email_verified=True
):
    auth = SaasUserAuth(
        user_id=str(USER_ID),
        principal=Principal(USER_ID, method, datetime.now(UTC), restricted=restricted),
        accepted_tos=accepted_tos,
        email_verified=email_verified,
        auth_type=AuthType.BEARER if method == 'api_key' else AuthType.COOKIE,
    )
    req.state.user_auth = auth
    req.state.authentication_via_cookie = method != 'api_key'
    return auth


@pytest.mark.parametrize(
    'path',
    [
        '/api/auth/capabilities',
        '/api/auth/csrf',
        '/api/auth/login',
        '/api/auth/password/forgot',
        '/api/auth/password/reset',
        '/api/auth/email/verify',
        '/api/auth/invitations/enroll',
        '/api/options/config',
        '/api/v1/web-client/config',
        '/api/email/resend',
        '/api/v1/webhooks/a/secrets',
        '/oauth/device/authorize',
        '/oauth/device/token',
        '/api/refresh-tokens',
        '/api/v1/sandboxes/box/settings/secrets',
        '/api/v1/sandboxes/box/settings/secrets/GITHUB_TOKEN',
        '/login',
    ],
)
async def test_routes_with_separate_proof_or_public_access_do_not_require_browser_auth(
    path,
):
    req = request(path)
    downstream = AsyncMock(return_value=Response(status_code=204))
    assert (await SetAuthCookieMiddleware()(req, downstream)).status_code == 204
    downstream.assert_awaited_once_with(req)


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/webhooksettings',
        '/api/v1/sandboxes/box/settings',
        '/api/v1/sandboxes/box/settings/secrets/name/extra',
        '/oauth/device/verify-authenticated',
    ],
)
def test_similar_paths_do_not_bypass_account_authentication(path):
    assert SetAuthCookieMiddleware()._should_attach(request(path))


@pytest.mark.parametrize(
    'exc, expected',
    [
        (InvalidCredentials(), 401),
        (AuthError(), 401),
        (TokenRefreshError(), 503),
        (AuthenticationUnavailable(), 503),
    ],
)
async def test_authentication_errors_do_not_revoke_upstream_sessions(
    monkeypatch, exc, expected
):
    monkeypatch.setattr('server.middleware.get_user_auth', AsyncMock(side_effect=exc))
    logout = AsyncMock()
    monkeypatch.setattr('server.auth.saas_user_auth.token_manager.logout', logout)
    req = request(cookies={'keycloak_auth': 'signed-cookie'})
    downstream = AsyncMock()
    result = await SetAuthCookieMiddleware()(req, downstream)
    assert result.status_code == expected
    downstream.assert_not_awaited()
    logout.assert_not_awaited()
    assert ('set-cookie' in result.headers) is (expected == 401)


async def test_refresh_replaces_cookie_for_keycloak_principal(monkeypatch):
    req = request(cookies={'keycloak_auth': 'signed-cookie'})
    auth = authenticate(req)
    auth.refreshed = True
    auth.access_token = SecretStr('new-access')
    auth.refresh_token = SecretStr('new-refresh')
    from unittest.mock import MagicMock

    set_cookie = MagicMock()
    monkeypatch.setattr('server.middleware.set_response_cookie', set_cookie)
    monkeypatch.setattr('server.middleware.schedule_gitlab_repo_sync', MagicMock())
    result = await SetAuthCookieMiddleware()(req, AsyncMock(return_value=Response()))
    assert result.status_code == 200
    assert set_cookie.call_args.kwargs['keycloak_refresh_token'] == 'new-refresh'


@pytest.mark.parametrize('name', ['keycloak_auth', 'keycloak_auth_1', 'oh_session'])
async def test_explicit_session_response_is_not_overwritten_by_refresh(
    monkeypatch, name
):
    req = request(cookies={'keycloak_auth': 'signed-cookie'})
    auth = authenticate(req)
    auth.refreshed = True
    auth.access_token = SecretStr('refreshed-access')
    auth.refresh_token = SecretStr('refreshed-token')
    response = Response()
    response.set_cookie(name, 'route-owned-session')
    from unittest.mock import MagicMock

    write_cookie = MagicMock()
    monkeypatch.setattr('server.middleware.set_response_cookie', write_cookie)
    result = await SetAuthCookieMiddleware()(req, AsyncMock(return_value=response))
    assert result.status_code == 200
    assert 'route-owned-session' in result.headers['set-cookie']
    write_cookie.assert_not_called()


async def test_initial_password_restriction_precedes_terms_and_csrf():
    req = request('/api/authenticate', method='POST')
    authenticate(
        req,
        method='password',
        restricted=True,
        accepted_tos=False,
        email_verified=False,
    )
    downstream = AsyncMock()
    result = await SetAuthCookieMiddleware()(req, downstream)
    assert result.status_code == 403
    assert b'password_change_required' in result.body
    downstream.assert_not_awaited()


@pytest.mark.parametrize('path', ['/api/auth/password/change', '/api/logout'])
async def test_restricted_session_can_change_password_or_logout(monkeypatch, path):
    req = request(path, method='POST')
    authenticate(
        req,
        method='password',
        restricted=True,
        accepted_tos=False,
        email_verified=False,
    )
    from unittest.mock import MagicMock

    csrf = MagicMock()
    monkeypatch.setattr('server.middleware.validate_csrf', csrf)
    result = await SetAuthCookieMiddleware()(req, AsyncMock(return_value=Response()))
    assert result.status_code == 200
    if path == '/api/logout':
        # Logout owns cookie CSRF validation so unavailable upstream authentication
        # cannot prevent deleting an expired session.
        csrf.assert_not_called()
    else:
        csrf.assert_called_once_with(req)


async def test_terms_and_keycloak_email_checks_precede_endpoint_side_effects():
    for accepted_tos, verified in [(False, True), (True, False)]:
        req = request()
        authenticate(req, accepted_tos=accepted_tos, email_verified=verified)
        downstream = AsyncMock()
        result = await SetAuthCookieMiddleware()(req, downstream)
        assert result.status_code == 403
        downstream.assert_not_awaited()


async def test_explicitly_provisioned_local_account_does_not_require_email_proof():
    req = request()
    authenticate(req, method='password', email_verified=False)
    assert (
        await SetAuthCookieMiddleware()(req, AsyncMock(return_value=Response()))
    ).status_code == 200


async def test_real_api_key_request_ignores_browser_terms_and_csrf():
    req = request(method='POST')
    authenticate(req, method='api_key', accepted_tos=False, email_verified=False)
    assert (
        await SetAuthCookieMiddleware()(req, AsyncMock(return_value=Response()))
    ).status_code == 200


@pytest.mark.parametrize(
    'exc, expected',
    [(ProviderReconnectRequired(), 409), (AuthenticationUnavailable(), 503)],
)
async def test_provider_failure_preserves_browser_session(exc, expected):
    req = request(cookies={'keycloak_auth': 'signed-cookie'})
    authenticate(req)
    result = await SetAuthCookieMiddleware()(req, AsyncMock(side_effect=exc))
    assert result.status_code == expected
    assert 'set-cookie' not in result.headers
