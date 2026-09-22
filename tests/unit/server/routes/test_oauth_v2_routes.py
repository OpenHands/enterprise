"""Route tests for the Phase 1 OAuth v2 callback routes (OHE-3294).

These exercise the routes in isolation:

- encrypted-state roundtrip (``_encrypt_state`` / ``_decrypt_state``),
- ``GET /oauth/{provider_id}/login`` redirect + state encoding,
- ``GET /oauth/providers`` list,
- ``GET /oauth/{provider_id}/callback`` login flow with mocked token
  exchange + userinfo, asserting tokens are persisted and the cookie set,
- ``GET /oauth/{provider_id}/callback`` link flow,
- ``DELETE /oauth/{provider_id}`` unlink (authenticated),
- error handling (missing code/state, bad state, unknown provider).

External HTTP (token exchange, userinfo) and stores are mocked so the routes
run without a database or live OAuth provider.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.routes import oauth_v2


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(
        kid='test', key=SecretStr('test-secret-key-for-testing'), active=True
    )
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@pytest.fixture
def app(jwt_svc):
    """FastAPI app with the oauth_v2 router and encryption patched."""
    application = FastAPI()
    application.include_router(oauth_v2.oauth_v2_router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _override_user_context(app, user_id):
    """Override ``get_user_id`` so the route sees ``user_id`` (or ``None``)."""
    app.dependency_overrides[get_user_id] = lambda: (str(user_id) if user_id else None)


def _fake_provider(
    *,
    provider_id: int = 1,
    category: str = 'github',
    is_idp: bool = False,
    client_secret: str | None = 'secret',
    userinfo_url: str | None = 'https://example.com/user',
):
    provider = MagicMock()
    provider.id = provider_id
    provider.provider_category = category
    provider.display_name = category.title()
    provider.is_idp = is_idp
    provider.client_id = 'cid'
    provider.client_secret = {'v': client_secret} if client_secret else None
    provider.authorization_url = 'https://example.com/auth'
    provider.token_url = 'https://example.com/token'
    provider.userinfo_url = userinfo_url
    provider.scopes = ['repo']
    provider.permitted_drift_seconds = 60
    return provider


# ── state roundtrip ──────────────────────────────────────────────────────


def test_state_roundtrip(jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        payload = {'redirect_url': '/foo', 'mode': 'link', 'user_id': 'abc'}
        encrypted = oauth_v2._encrypt_state(payload)
        assert encrypted != json.dumps(payload)
        decrypted = oauth_v2._decrypt_state(encrypted)
        assert decrypted == payload


def test_decrypt_bad_state_raises(jwt_svc, client):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        # Garbage base64/state → 400.
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': '!!!not-valid!!!'},
        )
        assert response.status_code == 400


# ── login redirect ────────────────────────────────────────────────────────


def test_login_redirect_encodes_state(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/1/login',
            params={'redirect_url': '/dashboard', 'mode': 'login'},
            follow_redirects=False,
        )
    assert response.status_code == 307
    location = response.headers['location']
    assert location.startswith('https://example.com/auth')
    assert 'client_id=cid' in location
    assert 'scope=repo' in location
    assert 'state=' in location


def test_login_unknown_provider_404(client, jwt_svc):
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.get('/oauth/9999/login', follow_redirects=False)
    assert response.status_code == 404


def test_login_missing_auth_url_400(client, jwt_svc):
    provider = _fake_provider()
    provider.authorization_url = None
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/oauth/1/login', follow_redirects=False)
    assert response.status_code == 400


# ── providers list ───────────────────────────────────────────────────────


def test_list_providers(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'list_all',
            new=AsyncMock(return_value=[provider]),
        ),
    ):
        response = client.get('/oauth/providers')
    assert response.status_code == 200
    body = response.json()
    assert body['providers'][0]['id'] == 1
    assert body['providers'][0]['provider_category'] == 'github'


# ── callback login flow ──────────────────���──────────────────────────────


def _patch_httpx(token_response: dict, userinfo: dict):
    """Patch ``httpx.AsyncClient`` so POST (token) and GET (userinfo) return
    canned JSON without hitting the network."""
    post_response = MagicMock()
    post_response.status_code = 200
    post_response.headers = {'content-type': 'application/json'}
    post_response.json.return_value = token_response

    get_response = MagicMock()
    get_response.status_code = 200
    get_response.headers = {'content-type': 'application/json'}
    get_response.json.return_value = userinfo

    client_mock = AsyncMock()
    client_mock.post = AsyncMock(return_value=post_response)
    client_mock.get = AsyncMock(return_value=get_response)

    def _ctor(*args, **kwargs):
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        return client_mock

    return patch('server.routes.oauth_v2.httpx.AsyncClient', side_effect=_ctor)


def test_callback_login_persists_tokens_and_sets_cookie(client, jwt_svc):
    provider = _fake_provider()
    user_id = uuid4()
    token_response = {
        'access_token': 'at-123',
        'refresh_token': 'rt-123',
        'expires_in': 3600,
    }
    userinfo = {'sub': 'ext-sub-1', 'email': 'a@b.com'}

    fake_user = MagicMock()
    fake_user.id = user_id

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ) as store_tokens,
    ):
        # First build a login state, then call callback.
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers['location'] == '/done'
    # Cookie set on login flow.
    assert 'openhands_auth' in response.cookies
    # Token pair persisted.
    store_tokens.assert_awaited_once()
    kwargs = store_tokens.call_args.kwargs
    assert kwargs['access_token'] == 'at-123'
    assert kwargs['refresh_token'] == 'rt-123'
    assert kwargs['access_token_expires_at'] is not None


def test_callback_link_flow_no_cookie(client, jwt_svc):
    provider = _fake_provider()
    user_id = uuid4()
    token_response = {'access_token': 'at', 'refresh_token': 'rt'}
    userinfo = {'sub': 'ext-2'}

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ) as link,
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ),
    ):
        state = oauth_v2._encrypt_state(
            {
                'redirect_url': '/settings',
                'mode': 'link',
                'user_id': str(user_id),
                'nonce': 'n',
            }
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    # No auth cookie on link flow.
    assert 'openhands_auth' not in response.cookies
    link.assert_awaited_once()


def test_callback_missing_code_400(client, jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        response = client.get(
            '/oauth/1/callback', params={'state': 'x'}, follow_redirects=False
        )
    assert response.status_code == 400


def test_callback_error_param_400(client, jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        response = client.get(
            '/oauth/1/callback',
            params={'error': 'access_denied'},
            follow_redirects=False,
        )
    assert response.status_code == 400


def test_callback_link_missing_user_id_in_state_400(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx({'access_token': 'at'}, {'sub': 'x'}),
        patch.object(oauth_v2.OAuthTokenStore, 'store_tokens', new=AsyncMock()),
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/s', 'mode': 'link', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )
    assert response.status_code == 400


# ── link (authenticated) ────────────────────────────────────────────────


def test_link_requires_auth(app, client, jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        _override_user_context(app, None)
        response = client.post('/oauth/1/link')
    assert response.status_code == 401


def test_link_idp_provider_400(app, client, jwt_svc):
    provider = _fake_provider(is_idp=True)
    user_id = uuid4()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        _override_user_context(app, user_id)
        response = client.post('/oauth/1/link')
    assert response.status_code == 400


def test_link_returns_start_url(app, client, jwt_svc):
    provider = _fake_provider()
    user_id = uuid4()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        _override_user_context(app, user_id)
        response = client.post('/oauth/1/link')
    assert response.status_code == 200
    body = response.json()
    assert 'start_url' in body
    assert 'mode=link' in body['start_url']


# ── unlink ───────────────────────────────────────────────────────────────


def test_unlink_requires_auth(app, client, jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        _override_user_context(app, None)
        response = client.delete('/oauth/1')
    assert response.status_code == 401


def test_unlink_idp_400(app, client, jwt_svc):
    provider = _fake_provider(is_idp=True)
    user_id = uuid4()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        _override_user_context(app, user_id)
        response = client.delete('/oauth/1')
    assert response.status_code == 400


def test_unlink_git_provider(app, client, jwt_svc):
    provider = _fake_provider()
    user_id = uuid4()
    link_row = MagicMock()
    link_row.external_subject_id = 'ext-1'
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.OAuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get_by_user',
            new=AsyncMock(return_value=link_row),
        ),
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'unlink',
            new=AsyncMock(),
        ) as unlink,
        patch.object(
            oauth_v2.OAuthTokenStore,
            'delete_tokens',
            new=AsyncMock(),
        ) as delete_tokens,
    ):
        _override_user_context(app, user_id)
        response = client.delete('/oauth/1')
    assert response.status_code == 204
    unlink.assert_awaited_once()
    delete_tokens.assert_awaited_once()
