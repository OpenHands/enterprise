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
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.routes import oauth_v2
from storage.auth_provider_details import OAuthProviderDetails, SamlProviderDetails


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
    legacy_keycloak_type: str | None = None,
):
    provider = MagicMock()
    provider.id = provider_id
    provider.kind = OAuthProviderDetails.__name__
    provider.title = category.title()
    provider.is_idp = is_idp
    provider.details = OAuthProviderDetails(
        provider_type=ProviderType(category),
        client_id='cid',
        client_secret=SecretStr(client_secret) if client_secret else None,
        authorization_url='https://example.com/auth',
        token_url='https://example.com/token',
        userinfo_url=userinfo_url,
        scopes=['repo'],
        permitted_drift_seconds=60,
        legacy_keycloak_type=(
            ProviderType(legacy_keycloak_type) if legacy_keycloak_type else None
        ),
    )
    return provider


def _fake_saml_provider(*, provider_id: int = 5):
    provider = MagicMock()
    provider.id = provider_id
    provider.kind = SamlProviderDetails.__name__
    provider.title = 'Acme SSO'
    provider.is_idp = True
    provider.details = SamlProviderDetails()
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
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
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
            oauth_v2.AuthProviderStore,
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
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.get('/oauth/9999/login', follow_redirects=False)
    assert response.status_code == 404


def test_login_saml_provider_redirects_to_saml_router(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/5/login',
            params={'redirect_url': '/dashboard'},
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers['location']
    assert '/saml/5/login' in location
    assert 'redirect_url=%2Fdashboard' in location


def test_callback_saml_provider_404(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/5/callback',
            params={'code': 'c', 'state': 's'},
            follow_redirects=False,
        )
    assert response.status_code == 404


def _fake_unknown_kind_provider(*, provider_id: int = 6):
    provider = MagicMock()
    provider.id = provider_id
    provider.kind = 'FutureProviderDetails'
    provider.details = object()
    return provider


@pytest.mark.parametrize('path', ['login', 'callback'])
def test_unknown_provider_kind_404(client, jwt_svc, path):
    provider = _fake_unknown_kind_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            f'/oauth/6/{path}',
            params={'code': 'c', 'state': 's'},
            follow_redirects=False,
        )
    assert response.status_code == 404


def test_login_missing_auth_url_400(client, jwt_svc):
    provider = _fake_provider()
    provider.details.authorization_url = None
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/oauth/1/login', follow_redirects=False)
    assert response.status_code == 400


# ── idp-login / provider-type login redirects (OHE-3379) ────────────────


def test_idp_login_redirects_to_first_idp(client, jwt_svc):
    provider = _fake_provider(provider_id=7, category='enterprise_sso', is_idp=True)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_first_idp',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/idp-login',
            params={'redirect_url': '/dashboard'},
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers['location']
    assert location.endswith('/oauth/7/login?redirect_url=%2Fdashboard')


def test_idp_login_no_idp_configured_404(client, jwt_svc):
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_first_idp',
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.get('/oauth/idp-login', follow_redirects=False)
    assert response.status_code == 404


def test_idp_login_forwards_mode_link(client, jwt_svc):
    provider = _fake_provider(provider_id=3, is_idp=True)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_first_idp',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/idp-login',
            params={'mode': 'link', 'redirect_url': '/settings'},
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers['location']
    assert location.startswith('https://')
    assert '/oauth/3/login' in location
    assert 'mode=link' in location
    assert 'redirect_url=%2Fsettings' in location


# ── providers list ───────────────────────────────────────────────────────


def test_list_providers(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'list_all',
            new=AsyncMock(return_value=[provider]),
        ),
    ):
        response = client.get('/oauth/providers')
    assert response.status_code == 200
    body = response.json()
    assert body['providers'][0]['id'] == 1
    assert body['providers'][0]['kind'] == OAuthProviderDetails.__name__
    assert body['providers'][0]['title'] == 'Github'
    assert body['providers'][0]['is_idp'] is False


def test_list_git_providers_requires_auth(client, app):
    _override_user_context(app, None)
    response = client.get('/oauth/git-providers')
    assert response.status_code == 401


def test_list_git_providers_reports_connected_from_oauth_tokens(client, app, jwt_svc):
    user_id = uuid4()
    _override_user_context(app, user_id)
    github = _fake_provider(provider_id=1, category='github')
    gitlab = _fake_provider(provider_id=2, category='gitlab')
    for provider in (github, gitlab):
        provider.icon = None
        provider.color = '#000000'

    async def fake_get_raw(store):
        return object() if store.oauth_provider_id == 1 else None

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_git_providers',
            new=AsyncMock(return_value=[github, gitlab]),
        ),
        patch.object(oauth_v2.OAuthTokenStore, 'get_raw', new=fake_get_raw),
    ):
        response = client.get('/oauth/git-providers')

    assert response.status_code == 200
    body = response.json()
    assert [(p['id'], p['provider_type'], p['connected']) for p in body] == [
        (1, 'github', True),
        (2, 'gitlab', False),
    ]
    assert 'client_id' not in body[0]


def test_list_git_providers_empty_when_none_defined(client, app, jwt_svc):
    _override_user_context(app, uuid4())
    with patch.object(
        oauth_v2.AuthProviderStore,
        'get_git_providers',
        new=AsyncMock(return_value=[]),
    ):
        response = client.get('/oauth/git-providers')
    assert response.status_code == 200
    assert response.json() == []


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
    fake_user.accepted_tos = datetime.now(timezone.utc)

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ) as create_user,
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'is_user_disabled',
            new=AsyncMock(return_value=False),
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
    # 'ext-sub-1' is not a UUID -- the external sub must never be passed
    # through as the internal User.id (UserStore.create_user requires a
    # real UUID and derives Org.id from it).
    created_user_id = create_user.await_args.args[0]
    UUID(created_user_id)
    assert created_user_id != 'ext-sub-1'
    # Cookie set on login flow.
    assert 'openhands_auth' in response.cookies
    # Token pair persisted.
    store_tokens.assert_awaited_once()
    kwargs = store_tokens.call_args.kwargs
    assert kwargs['access_token'] == 'at-123'
    assert kwargs['refresh_token'] == 'rt-123'
    assert kwargs['access_token_expires_at'] is not None


class _FakeTokenStore:
    """Records every ``OAuthTokenStore(...)`` construction + ``store_tokens``
    call, keyed by ``oauth_provider_id`` -- unlike patching ``store_tokens``
    as a single shared ``AsyncMock`` (which can't tell which provider row a
    given call targeted, since ``self`` isn't part of a method replaced at
    the class level), this can distinguish the login IDP row's own token
    store from the git-provider row's.
    """

    instances: list[_FakeTokenStore] = []

    def __init__(self, *, user_id, oauth_provider_id):
        self.user_id = user_id
        self.oauth_provider_id = oauth_provider_id
        self.store_tokens = AsyncMock()
        _FakeTokenStore.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    @classmethod
    def calls_for(cls, oauth_provider_id: int) -> list:
        return [
            i.store_tokens.call_args
            for i in cls.instances
            if i.oauth_provider_id == oauth_provider_id and i.store_tokens.await_count
        ]


# ── callback login flow: legacy_keycloak_type git-provider token fetch ───────


def test_callback_login_legacy_keycloak_stores_real_git_token(client, jwt_svc):
    """A login via a row with ``legacy_keycloak_type`` set also fetches the real
    GitHub token from Keycloak's broker endpoint and stores it under the
    git-provider row for that brand, in addition to the Keycloak
    token pair stored under the login row itself."""
    provider = _fake_provider(
        provider_id=1,
        category='enterprise_sso',
        is_idp=True,
        legacy_keycloak_type='github',
    )
    git_provider = MagicMock()
    git_provider.id = 42
    user_id = uuid4()
    token_response = {
        'access_token': 'kc-at',
        'refresh_token': 'kc-rt',
        'expires_in': 3600,
    }
    userinfo = {'sub': 'ext-sub-1', 'email': 'a@b.com'}
    fake_user = MagicMock()
    fake_user.id = user_id
    fake_user.accepted_tos = datetime.now(timezone.utc)

    _FakeTokenStore.reset()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_provider_by_type',
            new=AsyncMock(return_value=git_provider),
        ) as get_provider_by_type,
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore, 'get', new=AsyncMock(return_value=None)
        ),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
        patch.object(
            oauth_v2.UserStore, 'create_user', new=AsyncMock(return_value=fake_user)
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore, 'is_user_disabled', new=AsyncMock(return_value=False)
        ),
        patch.object(oauth_v2, 'OAuthTokenStore', _FakeTokenStore),
        patch.object(
            oauth_v2.TokenManager,
            'get_idp_tokens_from_keycloak',
            new=AsyncMock(
                return_value={
                    'access_token': 'gh-at',
                    'refresh_token': 'gh-rt',
                    'access_token_expires_at': 0,
                    'refresh_token_expires_at': 0,
                }
            ),
        ) as get_idp_tokens_from_keycloak,
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    get_provider_by_type.assert_awaited_once_with('github')
    get_idp_tokens_from_keycloak.assert_awaited_once_with('kc-at', ProviderType.GITHUB)
    # Keycloak token pair persisted under the login row itself.
    login_calls = _FakeTokenStore.calls_for(1)
    assert len(login_calls) == 1
    assert login_calls[0].kwargs['access_token'] == 'kc-at'
    # The real GitHub token persisted under the GitHub git-provider row.
    git_calls = _FakeTokenStore.calls_for(42)
    assert len(git_calls) == 1
    assert git_calls[0].kwargs['access_token'] == 'gh-at'
    assert git_calls[0].kwargs['refresh_token'] == 'gh-rt'
    assert git_calls[0].kwargs['access_token_expires_at'] is None
    assert git_calls[0].kwargs['refresh_token_expires_at'] is None


def test_callback_login_legacy_keycloak_no_git_provider_row_is_graceful(
    client, jwt_svc
):
    """No git-provider row for the brand (e.g. deleted since
    the login row was seeded) -- login still succeeds, just without a git
    credential being stored."""
    provider = _fake_provider(
        provider_id=1,
        category='enterprise_sso',
        is_idp=True,
        legacy_keycloak_type='github',
    )
    user_id = uuid4()
    token_response = {'access_token': 'kc-at', 'refresh_token': 'kc-rt'}
    userinfo = {'sub': 'ext-sub-1'}
    fake_user = MagicMock()
    fake_user.id = user_id
    fake_user.accepted_tos = datetime.now(timezone.utc)

    _FakeTokenStore.reset()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_provider_by_type',
            new=AsyncMock(return_value=None),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore, 'get', new=AsyncMock(return_value=None)
        ),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
        patch.object(
            oauth_v2.UserStore, 'create_user', new=AsyncMock(return_value=fake_user)
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore, 'is_user_disabled', new=AsyncMock(return_value=False)
        ),
        patch.object(oauth_v2, 'OAuthTokenStore', _FakeTokenStore),
        patch.object(
            oauth_v2.TokenManager,
            'get_idp_tokens_from_keycloak',
            new=AsyncMock(),
        ) as get_idp_tokens_from_keycloak,
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    get_idp_tokens_from_keycloak.assert_not_awaited()
    # Only the login row's own Keycloak token pair was stored.
    assert len(_FakeTokenStore.instances) == 1


def test_callback_login_non_legacy_keycloak_skips_broker_fetch(client, jwt_svc):
    """A login IDP with no ``legacy_keycloak_type`` is unaffected -- no
    broker call, no git-provider row lookup."""
    provider = _fake_provider(provider_id=1, category='enterprise_sso', is_idp=True)
    user_id = uuid4()
    token_response = {'access_token': 'at-123', 'refresh_token': 'rt-123'}
    userinfo = {'sub': 'ext-sub-1'}
    fake_user = MagicMock()
    fake_user.id = user_id
    fake_user.accepted_tos = datetime.now(timezone.utc)

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_provider_by_type',
            new=AsyncMock(),
        ) as get_provider_by_type,
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore, 'get', new=AsyncMock(return_value=None)
        ),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
        patch.object(
            oauth_v2.UserStore, 'create_user', new=AsyncMock(return_value=fake_user)
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore, 'is_user_disabled', new=AsyncMock(return_value=False)
        ),
        patch.object(oauth_v2.OAuthTokenStore, 'store_tokens', new=AsyncMock()),
        patch.object(
            oauth_v2.TokenManager,
            'get_idp_tokens_from_keycloak',
            new=AsyncMock(),
        ) as get_idp_tokens_from_keycloak,
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    get_provider_by_type.assert_not_awaited()
    get_idp_tokens_from_keycloak.assert_not_awaited()


def test_callback_login_redirects_to_tos_when_not_accepted(client, jwt_svc):
    """A login for a user who has not accepted the TOS redirects to /accept-tos."""
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
    fake_user.accepted_tos = None  # TOS not yet accepted

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'is_user_disabled',
            new=AsyncMock(return_value=False),
        ),
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ),
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    location = response.headers['location']
    assert '/accept-tos' in location
    assert 'redirect_url=%2Fdone' in location
    # Cookie is still set (with accepted_tos=False) so the TOS page can accept.
    assert 'openhands_auth' in response.cookies


def test_callback_login_refuses_a_disabled_user(client, jwt_svc):
    """A disabled user is sent to the login page: no tokens stored, no cookie."""
    # Arrange
    provider = _fake_provider()
    token_response = {
        'access_token': 'at-123',
        'refresh_token': 'rt-123',
        'expires_in': 3600,
    }
    userinfo = {'sub': 'ext-sub-1', 'email': 'a@b.com'}
    fake_user = MagicMock()
    fake_user.id = uuid4()

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'is_user_disabled',
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ) as store_tokens,
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )

        # Act
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    # Assert
    assert response.status_code == 302
    assert response.headers['location'].endswith('/login?account_disabled=true')
    assert 'openhands_auth' not in response.cookies
    store_tokens.assert_not_awaited()


def test_callback_login_grants_super_admin_for_configured_email(client, jwt_svc):
    """A login whose email is in SUPER_ADMIN_EMAILS is promoted every time.

    Covers both the new-account case and an email added to
    ``SUPER_ADMIN_EMAILS`` after the account already existed -- the callback
    doesn't distinguish the two, it just self-heals on every login.
    """
    provider = _fake_provider()
    user_id = uuid4()
    token_response = {
        'access_token': 'at-123',
        'refresh_token': 'rt-123',
        'expires_in': 3600,
    }
    userinfo = {'sub': 'ext-sub-1', 'email': 'Admin@Example.com'}

    fake_user = MagicMock()
    fake_user.id = user_id
    fake_user.email = 'Admin@Example.com'
    fake_user.accepted_tos = datetime.now(timezone.utc)

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'is_user_disabled',
            new=AsyncMock(return_value=False),
        ),
        patch.object(
            oauth_v2.UserStore,
            'grant_super_admin',
            new=AsyncMock(return_value=fake_user),
        ) as grant_super_admin,
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ),
        # Matched case-insensitively against the configured list.
        patch.object(oauth_v2, 'SUPER_ADMIN_EMAILS', frozenset({'admin@example.com'})),
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    grant_super_admin.assert_awaited_once_with(str(user_id))


def test_callback_login_skips_super_admin_grant_when_email_not_configured(
    client, jwt_svc
):
    """A login whose email is not in SUPER_ADMIN_EMAILS is left untouched."""
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
    fake_user.email = 'a@b.com'
    fake_user.accepted_tos = datetime.now(timezone.utc)

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore,
            'is_user_disabled',
            new=AsyncMock(return_value=False),
        ),
        patch.object(
            oauth_v2.UserStore,
            'grant_super_admin',
            new=AsyncMock(),
        ) as grant_super_admin,
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ),
        patch.object(oauth_v2, 'SUPER_ADMIN_EMAILS', frozenset({'admin@example.com'})),
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    grant_super_admin.assert_not_awaited()


def _link_state(user_id, *, nonce: str = 'nonce-1', issued_at: float | None = None):
    return oauth_v2._encrypt_state(
        {
            'redirect_url': '/settings',
            'mode': 'link',
            'user_id': str(user_id),
            'nonce': nonce,
            'issued_at': time.time() if issued_at is None else issued_at,
        }
    )


def _nonce_cookie(nonce: str = 'nonce-1') -> dict[str, str]:
    return {'Cookie': f'{oauth_v2.LINK_NONCE_COOKIE}={nonce}'}


def test_callback_link_flow_with_matching_nonce_cookie(client, jwt_svc):
    provider = _fake_provider()
    user_id = uuid4()
    token_response = {'access_token': 'at', 'refresh_token': 'rt'}
    userinfo = {'sub': 'ext-2'}

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx(token_response, userinfo),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ) as link,
        patch.object(
            oauth_v2.OAuthTokenStore,
            'store_tokens',
            new=AsyncMock(),
        ) as store_tokens,
        patch.object(
            oauth_v2, 'invalidate_user_id_cache', new=AsyncMock()
        ) as invalidate_cache,
    ):
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': _link_state(user_id)},
            headers=_nonce_cookie(),
            follow_redirects=False,
        )

    assert response.status_code == 302
    # No auth cookie on link flow, and the one-shot nonce cookie is cleared.
    assert 'openhands_auth' not in response.cookies
    cleared = SimpleCookie(response.headers['set-cookie'])[oauth_v2.LINK_NONCE_COOKIE]
    assert cleared.value == ''
    assert cleared['max-age'] == '0'
    assert cleared['path'] == '/oauth'
    link.assert_awaited_once_with(
        oauth_provider_id=provider.id, user_id=user_id, external_subject_id='ext-2'
    )
    invalidate_cache.assert_awaited_once_with(ProviderType.GITHUB, 'ext-2')
    store_tokens.assert_awaited_once()


@pytest.mark.parametrize(
    ('headers', 'issued_at', 'detail'),
    [
        pytest.param(
            {}, None, 'Link flow was not started by this browser', id='no-nonce-cookie'
        ),
        pytest.param(
            _nonce_cookie('someone-else'),
            None,
            'Link flow was not started by this browser',
            id='mismatched-nonce',
        ),
        pytest.param(
            _nonce_cookie(),
            time.time() - oauth_v2.LINK_STATE_TTL_SECONDS - 1,
            'Link flow expired; start it again',
            id='expired-state',
        ),
    ],
)
def test_callback_link_rejects_state_not_bound_to_this_browser(
    client, jwt_svc, headers, issued_at, detail
):
    """A link ``start_url`` sent to another browser must not link that
    browser's provider identity to the user named in the state."""
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(oauth_v2, '_exchange_code', new=AsyncMock()) as exchange,
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()) as link,
        patch.object(
            oauth_v2.OAuthTokenStore, 'store_tokens', new=AsyncMock()
        ) as store_tokens,
    ):
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': _link_state(uuid4(), issued_at=issued_at)},
            headers=headers,
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert response.json()['detail'] == detail
    exchange.assert_not_awaited()
    link.assert_not_awaited()
    store_tokens.assert_not_awaited()


def test_callback_missing_code_400(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/1/callback', params={'state': 'x'}, follow_redirects=False
        )
    assert response.status_code == 400


def test_callback_error_param_400(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/oauth/1/callback',
            params={'error': 'access_denied'},
            follow_redirects=False,
        )
    assert response.status_code == 400


# ── callback: GitLab repo sync ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ('category', 'is_idp', 'flag', 'expect_sync'),
    [
        ('gitlab', False, 'oauth_v2_login_on', True),
        ('github', False, 'oauth_v2_login_on', False),
        ('gitlab', True, 'oauth_v2_login_on', False),
        ('gitlab', False, 'oauth_v2_login_off', False),
    ],
)
def test_callback_link_schedules_gitlab_repo_sync(
    request, client, jwt_svc, category, is_idp, flag, expect_sync
):
    request.getfixturevalue(flag)
    provider = _fake_provider(category=category, is_idp=is_idp)
    user_id = uuid4()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx({'access_token': 'at', 'refresh_token': 'rt'}, {'id': 42}),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
        patch.object(oauth_v2.OAuthTokenStore, 'store_tokens', new=AsyncMock()),
        patch.object(oauth_v2, 'schedule_gitlab_repo_sync') as schedule_sync,
    ):
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': _link_state(user_id)},
            headers=_nonce_cookie(),
            follow_redirects=False,
        )

    assert response.status_code == 302
    if expect_sync:
        schedule_sync.assert_called_once_with(str(user_id))
    else:
        schedule_sync.assert_not_called()


def test_callback_legacy_keycloak_gitlab_login_syncs_after_storing_real_token(
    oauth_v2_login_on, client, jwt_svc
):
    """The sync reads the GitLab token fetched from Keycloak, so it must start
    only after that token is stored."""
    provider = _fake_provider(
        provider_id=1,
        category='enterprise_sso',
        is_idp=True,
        legacy_keycloak_type='gitlab',
    )
    git_provider = MagicMock()
    git_provider.id = 42
    fake_user = MagicMock()
    fake_user.id = uuid4()
    fake_user.accepted_tos = datetime.now(timezone.utc)
    stored_git_tokens_at_sync: list[int] = []

    _FakeTokenStore.reset()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_provider_by_type',
            new=AsyncMock(return_value=git_provider),
        ),
        _patch_httpx(
            {'access_token': 'kc-at', 'refresh_token': 'kc-rt'},
            {'sub': 'ext-sub-1', 'email': 'a@b.com'},
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore, 'get', new=AsyncMock(return_value=None)
        ),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
        patch.object(
            oauth_v2.UserStore, 'create_user', new=AsyncMock(return_value=fake_user)
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            oauth_v2.UserStore, 'is_user_disabled', new=AsyncMock(return_value=False)
        ),
        patch.object(oauth_v2, 'OAuthTokenStore', _FakeTokenStore),
        patch.object(
            oauth_v2.TokenManager,
            'get_idp_tokens_from_keycloak',
            new=AsyncMock(
                return_value={'access_token': 'gl-at', 'refresh_token': 'gl-rt'}
            ),
        ),
        patch.object(
            oauth_v2,
            'schedule_gitlab_repo_sync',
            side_effect=lambda _user_id: stored_git_tokens_at_sync.append(
                len(_FakeTokenStore.calls_for(42))
            ),
        ) as schedule_sync,
    ):
        state = oauth_v2._encrypt_state(
            {'redirect_url': '/done', 'mode': 'login', 'nonce': 'n'}
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    schedule_sync.assert_called_once_with(str(fake_user.id))
    assert stored_git_tokens_at_sync == [1]


def test_callback_link_missing_user_id_in_state_400(client, jwt_svc):
    provider = _fake_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx({'access_token': 'at'}, {'sub': 'x'}),
        patch.object(oauth_v2.OAuthTokenStore, 'store_tokens', new=AsyncMock()),
    ):
        state = oauth_v2._encrypt_state(
            {
                'redirect_url': '/s',
                'mode': 'link',
                'nonce': 'nonce-1',
                'issued_at': time.time(),
            }
        )
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            headers=_nonce_cookie(),
            follow_redirects=False,
        )
    assert response.status_code == 400
    assert response.json()['detail'] == 'Link flow missing user_id in state'


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
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        _override_user_context(app, user_id)
        response = client.post('/oauth/1/link')
    assert response.status_code == 400


def test_link_unknown_provider_kind_404(app, client, jwt_svc):
    provider = _fake_unknown_kind_provider()
    provider.is_idp = False
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        _override_user_context(app, uuid4())
        response = client.post('/oauth/6/link')
    assert response.status_code == 404


def test_link_returns_start_url(app, client, jwt_svc):
    provider = _fake_provider()
    user_id = uuid4()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        _override_user_context(app, user_id)
        response = client.post('/oauth/1/link')
        assert response.status_code == 200
        start_url = urlsplit(response.json()['start_url'])
        query = parse_qs(start_url.query)
        state = oauth_v2._decrypt_state(query['state'][0])

    # The browser goes straight to the provider with a state the callback
    # accepts in link mode: it must carry the signed-in user's id.
    assert f'{start_url.scheme}://{start_url.netloc}{start_url.path}' == (
        'https://example.com/auth'
    )
    assert query['redirect_uri'] == ['https://testserver/oauth/1/callback']
    assert state['mode'] == 'link'
    assert state['user_id'] == str(user_id)
    assert state['redirect_url'] == 'https://testserver/settings/integrations'
    assert time.time() - state['issued_at'] < 60

    # The state is bound to this browser by a nonce cookie the provider's
    # cross-site redirect back to the callback still carries.
    cookie = SimpleCookie(response.headers['set-cookie'])[oauth_v2.LINK_NONCE_COOKIE]
    assert cookie.value == state['nonce']
    assert len(state['nonce']) >= 32
    assert cookie['httponly'] is True
    assert cookie['secure'] is True
    assert cookie['samesite'].lower() == 'lax'
    assert cookie['path'] == '/oauth'
    assert cookie['max-age'] == str(oauth_v2.LINK_NONCE_COOKIE_MAX_AGE)
    assert oauth_v2.LINK_NONCE_COOKIE_MAX_AGE > oauth_v2.LINK_STATE_TTL_SECONDS


def test_link_start_url_completes_in_the_browser_that_started_it(app, jwt_svc):
    # https, so the jar sends the Secure nonce cookie back; it applies Path too.
    client = TestClient(app, base_url='https://testserver')
    provider = _fake_provider()
    user_id = uuid4()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        _patch_httpx({'access_token': 'at', 'refresh_token': 'rt'}, {'id': 42}),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()) as link,
        patch.object(oauth_v2.OAuthTokenStore, 'store_tokens', new=AsyncMock()),
    ):
        _override_user_context(app, user_id)
        start = client.post('/oauth/1/link')
        state = parse_qs(urlsplit(start.json()['start_url']).query)['state'][0]
        response = client.get(
            '/oauth/1/callback',
            params={'code': 'c', 'state': state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    link.assert_awaited_once_with(
        oauth_provider_id=provider.id, user_id=user_id, external_subject_id='42'
    )


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
            oauth_v2.AuthProviderStore,
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
            oauth_v2.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get_by_user',
            new=AsyncMock(return_value=link_row),
        ),
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'unlink',
            new=AsyncMock(),
        ) as unlink,
        patch.object(
            oauth_v2.OAuthTokenStore,
            'delete_tokens',
            new=AsyncMock(),
        ) as delete_tokens,
        patch.object(
            oauth_v2, 'invalidate_user_id_cache', new=AsyncMock()
        ) as invalidate_cache,
    ):
        _override_user_context(app, user_id)
        response = client.delete('/oauth/1')
    assert response.status_code == 204
    unlink.assert_awaited_once()
    invalidate_cache.assert_awaited_once_with(ProviderType.GITHUB, 'ext-1')
    delete_tokens.assert_awaited_once()
