"""Logout owns cookie deletion even when its expired session needs refresh."""

import time
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from keycloak.exceptions import KeycloakConnectionError, KeycloakPostError

from server.auth import mode
from server.auth.authentication import AuthenticationService
from server.auth.cookie_chunking import CHUNK_SIZE
from server.middleware import SetAuthCookieMiddleware
from server.routes.auth import api_router
from server.routes.local_auth import router as local_router
from storage.encrypt_utils import get_jwt_service
from tests.unit.server.auth.test_shared_authentication import (
    auth_database as auth_database,
)


@pytest.fixture
async def browser(auth_database, monkeypatch):  # noqa: F811
    _, user, _ = auth_database
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    monkeypatch.setenv('WEB_HOST', 'https://app.example.com')
    monkeypatch.setattr('server.routes.auth.get_cookie_domain', lambda: None)
    upstream = MagicMock(a_logout=AsyncMock())
    monkeypatch.setattr(
        'server.auth.keycloak.token_manager.get_keycloak_openid', lambda *_: upstream
    )

    async def authenticate(request):
        cached = getattr(request.state, 'user_auth', None)
        if cached is None:
            cached = await AuthenticationService().authenticate_request(request)
            request.state.user_auth = cached
        return cached

    monkeypatch.setattr('server.routes.auth.get_user_auth', authenticate)
    monkeypatch.setattr('server.middleware.get_user_auth', authenticate)
    app = FastAPI()
    app.middleware('http')(SetAuthCookieMiddleware())
    app.include_router(api_router)
    app.include_router(local_router)

    @app.get('/api/v1/users/me')
    async def me():
        return {'authenticated': True}

    def access_token(expired=False, padding=''):
        return jwt.encode(
            {
                'sub': str(user.id),
                'exp': int(time.time()) + (-1 if expired else 3600),
                'padding': padding,
            },
            'synthetic-provider-signing-key-at-least-32-bytes',
        )

    upstream.a_refresh_token = AsyncMock(
        return_value={
            'access_token': access_token(),
            'refresh_token': 'rotated-refresh',
        }
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        yield client, upstream, access_token


def set_expired_cookie(client, access_token, chunked):
    cookie = get_jwt_service().create_jws_token(
        {
            'access_token': access_token(
                expired=True, padding='x' * 4000 if chunked else ''
            ),
            'refresh_token': 'browser-refresh',
            'accepted_tos': True,
        }
    )
    for index, offset in enumerate(range(0, len(cookie), CHUNK_SIZE)):
        name = 'keycloak_auth' + (f'_{index}' if index else '')
        client.cookies.set(
            name,
            cookie[offset : offset + CHUNK_SIZE],
            domain='app.example.com',
            path='/',
        )


@pytest.mark.parametrize('chunked', [False, True])
@pytest.mark.parametrize('failure', [None, 429, 503, 'connection'])
async def test_expired_cookie_logout_clears_browser_even_during_upstream_outage(
    browser, chunked, failure
):
    client, upstream, access_token = browser
    set_expired_cookie(client, access_token, chunked)
    if failure == 'connection':
        upstream.a_refresh_token.side_effect = KeycloakConnectionError('unavailable')
    elif failure is not None:
        upstream.a_refresh_token.side_effect = KeycloakPostError('unavailable', failure)
    csrf = (await client.get('/api/auth/csrf')).json()['csrf_token']
    response = await client.post(
        '/api/logout',
        headers={'Origin': 'https://app.example.com', 'X-CSRF-Token': csrf},
    )
    assert response.status_code == 200
    assert not any(name.startswith('keycloak_auth') for name in client.cookies)
    assert 'oh_csrf' not in client.cookies
    assert (await client.get('/api/v1/users/me')).status_code == 401
    upstream.a_refresh_token.assert_awaited()
    if failure is None:
        upstream.a_logout.assert_awaited_once_with(refresh_token='rotated-refresh')
    else:
        upstream.a_logout.assert_not_awaited()
    assert mode.get_auth_mode() is mode.AuthMode.KEYCLOAK


@pytest.mark.parametrize('invalid', ['csrf', 'origin'])
async def test_logout_still_requires_cookie_csrf_and_origin(browser, invalid):
    client, upstream, access_token = browser
    set_expired_cookie(client, access_token, True)
    csrf = (await client.get('/api/auth/csrf')).json()['csrf_token']
    response = await client.post(
        '/api/logout',
        headers={
            'Origin': 'https://elsewhere.example'
            if invalid == 'origin'
            else 'https://app.example.com',
            'X-CSRF-Token': 'invalid' if invalid == 'csrf' else csrf,
        },
    )
    assert response.status_code == 403
    assert 'keycloak_auth' in client.cookies
    upstream.a_refresh_token.assert_not_awaited()
    upstream.a_logout.assert_not_awaited()
