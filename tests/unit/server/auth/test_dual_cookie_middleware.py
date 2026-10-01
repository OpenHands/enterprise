"""Tests for the dual-cookie auth middleware (Phase 2, OHE-3295).

Covers:
- New ``openhands_auth`` JWT cookie is preferred over the legacy
  ``keycloak_auth`` chunked cookie.
- Re-mint of the v2 cookie when the IDP token is refreshed (old cookie
  untouched).
- Fallback to the legacy chunked cookie when no v2 cookie is present.
- Error paths delete the v2 cookie; v2 cookie sessions do NOT trigger
  Keycloak logout.
- ``_check_tos`` reads ``accepted_tos`` from the v2 cookie.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.auth_error import (
    AuthError,
)
from server.auth.saas_user_auth import SaasUserAuth
from server.middleware import OAUTH_V2_COOKIE_NAME, SetAuthCookieMiddleware


@contextmanager
def _mock_jwt_decode(accepted_tos: bool = True, user_id: str | None = 'user-1'):
    mock_svc = MagicMock()
    payload = {'accepted_tos': accepted_tos}
    if user_id is not None:
        payload['user_id'] = user_id
    mock_svc.verify_jws_token.return_value = payload
    with patch('storage.encrypt_utils.get_jwt_service', return_value=mock_svc):
        yield mock_svc


@pytest.fixture
def middleware():
    return SetAuthCookieMiddleware()


@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.cookies = {}
    request.headers = {}
    return request


@pytest.fixture
def mock_response():
    return MagicMock(spec=Response)


def _v2_user_auth(*, refreshed=False, accepted_tos=True, user_id='user-1'):
    ua = MagicMock(spec=SaasUserAuth)
    ua.auth_type = AuthType.COOKIE
    ua.oauth_v2_cookie = True
    ua.refreshed = refreshed
    ua.accepted_tos = accepted_tos
    ua.user_id = user_id
    ua.access_token_expires_at = None
    ua.idp_refresh_token_expires_at = None
    ua.email_verified = True
    ua.get_user_id = AsyncMock(return_value=user_id)
    return ua


# ── v2 cookie preferred, re-mint on refresh ──────────────────────────────


@pytest.mark.asyncio
async def test_v2_cookie_remint_on_refresh(middleware, mock_request, mock_response):
    """When a v2 cookie session refreshes, the ``openhands_auth`` cookie is re-mint."""
    mock_request.cookies = {OAUTH_V2_COOKIE_NAME: 'v2-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    user_auth = _v2_user_auth(refreshed=True)

    with (
        _mock_jwt_decode(),
        patch.object(middleware, '_get_user_auth', return_value=user_auth),
        patch('server.middleware._set_oauth_v2_cookie') as mock_set,
    ):
        result = await middleware(mock_request, AsyncMock(return_value=mock_response))

    assert result == mock_response
    mock_set.assert_called_once()
    kwargs = mock_set.call_args.kwargs
    assert kwargs['user_id'] == 'user-1'
    assert kwargs['accepted_tos'] is True


@pytest.mark.asyncio
async def test_v2_cookie_no_refresh_no_remint(middleware, mock_request, mock_response):
    """A v2 cookie session that did not refresh must not re-mint the cookie."""
    mock_request.cookies = {OAUTH_V2_COOKIE_NAME: 'v2-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    user_auth = _v2_user_auth(refreshed=False)

    with (
        _mock_jwt_decode(),
        patch.object(middleware, '_get_user_auth', return_value=user_auth),
        patch('server.middleware._set_oauth_v2_cookie') as mock_set,
    ):
        result = await middleware(mock_request, AsyncMock(return_value=mock_response))

    assert result == mock_response
    mock_set.assert_not_called()


# ── fallback to legacy cookie ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_cookie_refresh_rewrites_keycloak_auth(
    middleware, mock_request, mock_response
):
    """A legacy ``keycloak_auth`` cookie session still re-sets the chunked cookie."""
    mock_request.cookies = {'keycloak_auth': 'kc-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    user_auth = MagicMock(spec=SaasUserAuth)
    user_auth.auth_type = AuthType.COOKIE
    user_auth.oauth_v2_cookie = False
    user_auth.refreshed = True
    user_auth.access_token = SecretStr('new-at')
    user_auth.refresh_token = SecretStr('new-rt')
    user_auth.accepted_tos = True
    user_auth.email_verified = True
    user_auth.get_user_id = AsyncMock(return_value='user-2')

    with (
        _mock_jwt_decode(),
        patch.object(middleware, '_get_user_auth', return_value=user_auth),
        patch('server.middleware.set_response_cookie') as mock_set,
        patch('server.middleware.schedule_gitlab_repo_sync'),
    ):
        result = await middleware(mock_request, AsyncMock(return_value=mock_response))

    assert result == mock_response
    mock_set.assert_called_once()
    assert mock_set.call_args.kwargs['keycloak_access_token'] == 'new-at'


# ── error paths delete the right cookie ──────────────────────────────────


@pytest.mark.asyncio
async def test_auth_error_deletes_v2_cookie(middleware, mock_request):
    """On AuthError with a v2 cookie, the v2 cookie is deleted and Keycloak logout skipped."""
    mock_request.cookies = {OAUTH_V2_COOKIE_NAME: 'v2-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    with (
        _mock_jwt_decode(),
        patch.object(middleware, '_logout', new=AsyncMock()) as mock_logout,
    ):
        result = await middleware(mock_request, AsyncMock(side_effect=AuthError('bad')))

    assert isinstance(result, JSONResponse)
    assert result.status_code == status.HTTP_401_UNAUTHORIZED
    # V2 cookie sessions must not trigger Keycloak logout.
    mock_logout.assert_not_called()
    # The v2 cookie is deleted in the response.
    assert 'set-cookie' in result.headers


@pytest.mark.asyncio
async def test_auth_error_legacy_cookie_triggers_logout(middleware, mock_request):
    """On AuthError with a legacy cookie, Keycloak logout runs and the cookie is deleted."""
    mock_request.cookies = {'keycloak_auth': 'kc-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    with (
        _mock_jwt_decode(),
        patch.object(middleware, '_logout', new=AsyncMock()) as mock_logout,
    ):
        result = await middleware(mock_request, AsyncMock(side_effect=AuthError('bad')))

    assert result.status_code == status.HTTP_401_UNAUTHORIZED
    mock_logout.assert_awaited_once()
    assert 'set-cookie' in result.headers


# ── _logout skips v2 cookie sessions ─────────────────────────────────────


@pytest.mark.asyncio
async def test_logout_skips_v2_cookie_session():
    middleware = SetAuthCookieMiddleware()
    mock_request = MagicMock(spec=Request)

    v2_user_auth = _v2_user_auth()
    v2_user_auth.refresh_token = SecretStr('should-not-be-used')

    with (
        patch(
            'server.middleware.get_user_auth',
            new=AsyncMock(return_value=v2_user_auth),
        ),
        patch('server.middleware.token_manager.logout', new=AsyncMock()) as mock_kc,
    ):
        await middleware._logout(mock_request)

    mock_kc.assert_not_called()


# ── _check_tos reads v2 cookie ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_tos_v2_cookie_accepted(middleware, mock_request, mock_response):
    mock_request.cookies = {OAUTH_V2_COOKIE_NAME: 'v2-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    with _mock_jwt_decode(accepted_tos=True):
        result = await middleware(mock_request, AsyncMock(return_value=mock_response))
    assert result == mock_response


@pytest.mark.asyncio
async def test_check_tos_v2_cookie_declined_raises(middleware, mock_request):
    """A declined TOS (accepted_tos=False) on a v2 cookie surfaces as a 401.

    ``TosNotAcceptedError`` subclasses ``AuthError``, so it is caught by the
    generic ``AuthError`` handler and returns 401 (the v2 cookie is also
    deleted so the user must re-authenticate).
    """
    mock_request.cookies = {OAUTH_V2_COOKIE_NAME: 'v2-signed'}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    with _mock_jwt_decode(accepted_tos=False):
        result = await middleware(
            mock_request, AsyncMock(return_value=MagicMock(spec=Response))
        )
    assert result.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_no_credentials_when_no_cookie_and_no_api_key(middleware, mock_request):
    mock_request.cookies = {}
    mock_request.url = MagicMock()
    mock_request.url.hostname = 'localhost'
    mock_request.url.path = '/api/foo'
    mock_request.headers = {}

    result = await middleware(mock_request, AsyncMock())
    assert result.status_code == status.HTTP_401_UNAUTHORIZED
