"""Tests for the dev IDP module — an in-memory sentinel IDP that plugs into
the OAuth v2 flow as an email+password login (OHE-3381).

These tests exercise:

* ``derive_idp_user_id`` — deterministic, case-insensitive UUID derivation
* ``is_idp_available`` — gating logic (``ENABLE_INTEGRATED_IDP`` env var
  only; independent of whether a real IDP is also configured)
* ``get_idp_if_available`` — sentinel returned when available
* ``GET /oauth/idp-login`` — redirects to the dev IDP sentinel provider
* ``GET /oauth/{IDP_PROVIDER_ID}/login`` — redirects to the dedicated
  email+password pages instead of starting an OAuth flow
* ``GET /oauth/idp/login`` / ``GET /oauth/idp/signup`` — serve the
  HTML forms
* ``POST /oauth/idp/signup`` — creates an account, hashes the password,
  completes the login
* ``POST /oauth/idp/login`` — verifies the password, completes the login
* Error cases — 404 when unavailable, invalid credentials, taken email,
  password validation
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.password_hashing import hash_password
from server.routes import idp
from server.routes.idp import (
    IDP_INVITE_PATH,
    IDP_LOGIN_PATH,
    IDP_PROVIDER_ID,
    IDP_SIGNUP_PATH,
    SIGNUP_LINK_EXPIRY_HOURS,
    IdpProvider,
    derive_idp_user_id,
    get_idp_if_available,
    is_idp_available,
)

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """FastAPI app with idp_router and oauth_v2_router."""
    from server.routes import oauth_v2

    application = FastAPI()
    # Register idp first (as in saas_server.py) so its literal routes
    # match before the parameterized oauth_v2 routes.
    application.include_router(idp.idp_router)
    application.include_router(idp.idp_status_router)
    application.include_router(idp.idp_password_router)
    application.include_router(idp.idp_invite_router)
    application.include_router(oauth_v2.oauth_v2_router)
    return application


@contextmanager
def _authenticated_as(app, user_id: str | None):
    """Override ``get_user_id`` so the authenticated password endpoints see
    ``user_id`` as the current user (``None`` simulates no active session)."""
    from openhands.app_server.user_auth import get_user_id

    app.dependency_overrides[get_user_id] = lambda: user_id
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_user_id, None)


@pytest.fixture
def client(app):
    return TestClient(app)


def _mock_user(
    *,
    user_id: str | None = None,
    email: str = 'dev@example.com',
    accepted_tos=None,
    password_hash: str | None = None,
):
    """Create a mock User object."""
    import uuid

    user = MagicMock()
    user.id = uuid.UUID(user_id) if user_id else uuid.uuid4()
    user.email = email
    user.accepted_tos = accepted_tos
    user.user_consents_to_analytics = False
    user.password_hash = password_hash
    return user


@contextmanager
def _available(has_password_super_admin: bool = True):
    """Make the dev IDP available (``ENABLE_INTEGRATED_IDP`` env var on).

    ``has_password_super_admin`` simulates whether a super admin who can log
    in with a password already exists (default ``True``, the common case —
    login works normally, sign-up is unreachable) or not (``False`` —
    sign-up is the only reachable form, login redirects to it). The latter
    covers both "no super admin row at all" and "a super admin row exists
    but ``password_hash`` is still ``NULL``" — ``UserStore
    .has_super_admin_with_password()`` (patched here) treats both the same
    way. See the module docstring on ``server.routes.idp`` for the full
    state machine.
    """
    with ExitStack() as stack:
        stack.enter_context(patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True))
        stack.enter_context(
            patch(
                'server.routes.idp.UserStore.has_super_admin_with_password',
                new_callable=AsyncMock,
                return_value=has_password_super_admin,
            )
        )
        yield


def _patch_complete_login():
    return patch(
        'server.routes.idp._complete_idp_login',
        new_callable=AsyncMock,
    )


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(kid='test', key=SecretStr('test-secret-key'), active=True)
    return JwtService(keys=[key])


def _patch_jwt_service(jwt_svc: JwtService):
    """Patch the shared JWT service used by ``_create_signup_link_token`` /
    ``_verify_signup_link_token`` (both locally import
    ``storage.encrypt_utils.get_jwt_service``, same as ``oauth_v2_refresh``)."""
    return patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@contextmanager
def _superadmin():
    """Make the caller pass ``require_permission`` / ``authorize_permission``
    checks for any superadmin-only permission -- same technique as
    ``test_super_admins.py``: short-circuit the org-role lookup to ``None``
    and stack a ``superadmin`` super role on top."""
    superadmin = MagicMock()
    superadmin.name = 'admin'
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=superadmin),
        ),
    ):
        yield


# ── derive_idp_user_id ────────────────────────────────────────────────


class TestDeriveIdpUserId:
    def test_deterministic(self):
        """Same email always produces the same user id."""
        uid1 = derive_idp_user_id('dev@example.com')
        uid2 = derive_idp_user_id('dev@example.com')
        assert uid1 == uid2

    def test_case_insensitive(self):
        """Email case does not change the derived id."""
        uid1 = derive_idp_user_id('Dev@Example.COM')
        uid2 = derive_idp_user_id('dev@example.com')
        assert uid1 == uid2

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace does not change the derived id."""
        uid1 = derive_idp_user_id('  dev@example.com  ')
        uid2 = derive_idp_user_id('dev@example.com')
        assert uid1 == uid2

    def test_different_emails_different_ids(self):
        """Different emails produce different ids."""
        uid1 = derive_idp_user_id('alice@example.com')
        uid2 = derive_idp_user_id('bob@example.com')
        assert uid1 != uid2

    def test_returns_valid_uuid_string(self):
        """The derived id is a valid UUID string."""
        import uuid

        uid = derive_idp_user_id('dev@example.com')
        parsed = uuid.UUID(uid)
        assert parsed.version == 5


# ── is_idp_available ──────────────────────────────────────────────────


class TestIsIdpAvailable:
    def test_disabled_when_env_var_off(self):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            import asyncio

            result = asyncio.run(is_idp_available())
        assert result is False

    def test_enabled_no_idp(self):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
            import asyncio

            result = asyncio.run(is_idp_available())
        assert result is True

    def test_enabled_even_when_real_idp_configured(self):
        """The flag alone governs availability: a configured real IDP does
        not disable the dev IDP — ``get_first_idp`` uses this to prefer the
        dev IDP over the real one for ``/oauth/idp-login`` (see
        ``storage.oauth_provider_store``)."""
        with (
            patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            import asyncio

            result = asyncio.run(is_idp_available())
        assert result is True

    def test_env_var_accepts_1_as_truthy(self, monkeypatch):
        """Older Helm charts default to '1' rather than 'true'."""
        import importlib

        import server.constants

        monkeypatch.setenv('ENABLE_INTEGRATED_IDP', '1')
        importlib.reload(server.constants)
        assert server.constants.ENABLE_INTEGRATED_IDP is True

    @pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'True'])
    def test_env_var_truthy_values(self, monkeypatch, value):
        import importlib

        import server.constants

        monkeypatch.setenv('ENABLE_INTEGRATED_IDP', value)
        importlib.reload(server.constants)
        assert server.constants.ENABLE_INTEGRATED_IDP is True

    @pytest.mark.parametrize('value', ['0', 'false', '', 'no', None])
    def test_env_var_falsy_values(self, monkeypatch, value):
        import importlib

        import server.constants

        if value is None:
            monkeypatch.delenv('ENABLE_INTEGRATED_IDP', raising=False)
        else:
            monkeypatch.setenv('ENABLE_INTEGRATED_IDP', value)
        importlib.reload(server.constants)
        assert server.constants.ENABLE_INTEGRATED_IDP is False


# ── get_idp_if_available (sentinel) ────────────────────────────────────


class TestGetIdpIfAvailable:
    def test_returns_sentinel_when_available(self):
        with _available():
            import asyncio

            result = asyncio.run(get_idp_if_available())
        assert result is not None
        assert isinstance(result, IdpProvider)
        assert result.id == IDP_PROVIDER_ID

    def test_returns_none_on_cloud(self):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            import asyncio

            result = asyncio.run(get_idp_if_available())
        assert result is None

    def test_returns_sentinel_even_when_real_idp_configured(self):
        with (
            patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            import asyncio

            result = asyncio.run(get_idp_if_available())
        assert result is not None
        assert isinstance(result, IdpProvider)


# ── GET /oauth/idp-login redirect ─────────────────────────────────────────


class TestIdpLoginRedirect:
    def test_redirects_to_idp_sentinel(self, client):
        """``/oauth/idp-login`` redirects to ``/oauth/{IDP_PROVIDER_ID}/login``
        when the dev IDP sentinel is the only IDP available."""
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
            new_callable=AsyncMock,
            return_value=IdpProvider(),
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 302
        assert f'/oauth/{IDP_PROVIDER_ID}/login' in response.headers['location']

    def test_returns_404_when_no_idp_configured(self, client):
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 404


# ── GET /oauth/{IDP_PROVIDER_ID}/login redirects to the dedicated pages ──


class TestOAuthV2LoginRedirectsIdp:
    def test_redirects_to_idp_login_page(self, client):
        with _available():
            response = client.get(
                f'/oauth/{IDP_PROVIDER_ID}/login', follow_redirects=False
            )
        assert response.status_code == 302
        assert f'/oauth/{IDP_LOGIN_PATH}' in response.headers['location']

    def test_forwards_redirect_url(self, client):
        with _available():
            response = client.get(
                f'/oauth/{IDP_PROVIDER_ID}/login',
                params={'redirect_url': '/dashboard'},
                follow_redirects=False,
            )
        location = response.headers['location']
        assert 'redirect_url=%2Fdashboard' in location

    def test_404_when_unavailable(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.get(
                f'/oauth/{IDP_PROVIDER_ID}/login', follow_redirects=False
            )
        assert response.status_code == 404


# ── GET /oauth/{IDP_PROVIDER_ID}/callback 404s ────────────────────────


class TestOAuthV2CallbackRejectsIdp:
    def test_404_for_idp_provider(self, client):
        response = client.get(
            f'/oauth/{IDP_PROVIDER_ID}/callback',
            params={'code': 'x', 'state': 'y'},
            follow_redirects=False,
        )
        assert response.status_code == 404


# ── GET /oauth/idp/login (HTML form) ────────────────────────────────────


class TestIdpLoginForm:
    def test_serves_html_form(self, client):
        """When a super admin already exists, login renders normally."""
        with _available(has_password_super_admin=True):
            response = client.get(f'/oauth/{IDP_LOGIN_PATH}')
        assert response.status_code == 200
        assert 'text/html' in response.headers.get('content-type', '')
        assert 'email' in response.text.lower()
        assert 'password' in response.text.lower()
        assert 'Sign In' in response.text
        assert 'Development' not in response.text

    def test_404_when_unavailable(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.get(f'/oauth/{IDP_LOGIN_PATH}')
        assert response.status_code == 404

    def test_posts_to_login_path(self, client):
        with _available(has_password_super_admin=True):
            response = client.get(f'/oauth/{IDP_LOGIN_PATH}')
        assert f'/oauth/{IDP_LOGIN_PATH}' in response.text

    def test_no_signup_link_when_superadmin_exists(self, client):
        """Login is the *only* option once an admin account exists — no
        self-service sign-up link anywhere on the page."""
        with _available(has_password_super_admin=True):
            response = client.get(f'/oauth/{IDP_LOGIN_PATH}')
        assert f'/oauth/{IDP_SIGNUP_PATH}' not in response.text

    def test_shows_error_message(self, client):
        with _available(has_password_super_admin=True):
            response = client.get(
                f'/oauth/{IDP_LOGIN_PATH}', params={'error': 'invalid_credentials'}
            )
        assert 'Invalid email or password' in response.text

    def test_redirects_to_signup_when_no_superadmin(self, client):
        """No admin account yet — bootstrap (sign-up) is the only option."""
        with _available(has_password_super_admin=False):
            response = client.get(f'/oauth/{IDP_LOGIN_PATH}', follow_redirects=False)
        assert response.status_code == 302
        assert f'/oauth/{IDP_SIGNUP_PATH}' in response.headers['location']

    def test_redirect_to_signup_forwards_redirect_url(self, client):
        with _available(has_password_super_admin=False):
            response = client.get(
                f'/oauth/{IDP_LOGIN_PATH}',
                params={'redirect_url': '/dashboard'},
                follow_redirects=False,
            )
        assert 'redirect_url=%2Fdashboard' in response.headers['location']


# ── GET /oauth/idp/signup (HTML form) ───────────────────────────────────


class TestIdpSignupForm:
    def test_serves_html_form(self, client):
        """While no super admin exists, sign-up (bootstrap) renders normally."""
        with _available(has_password_super_admin=False):
            response = client.get(f'/oauth/{IDP_SIGNUP_PATH}')
        assert response.status_code == 200
        assert 'confirm_password' in response.text
        assert 'Create Admin Account' in response.text
        assert 'Development' not in response.text

    def test_404_when_unavailable(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.get(f'/oauth/{IDP_SIGNUP_PATH}')
        assert response.status_code == 404

    def test_no_login_link_while_bootstrapping(self, client):
        """Sign-up is the *only* option pre-bootstrap — no login link."""
        with _available(has_password_super_admin=False):
            response = client.get(f'/oauth/{IDP_SIGNUP_PATH}')
        assert f'/oauth/{IDP_LOGIN_PATH}' not in response.text

    def test_redirects_to_login_when_superadmin_exists(self, client):
        """An admin account already exists — sign-up is unreachable; the
        only option is to log in."""
        with _available(has_password_super_admin=True):
            response = client.get(f'/oauth/{IDP_SIGNUP_PATH}', follow_redirects=False)
        assert response.status_code == 302
        assert f'/oauth/{IDP_LOGIN_PATH}' in response.headers['location']

    def test_redirect_to_login_forwards_redirect_url(self, client):
        with _available(has_password_super_admin=True):
            response = client.get(
                f'/oauth/{IDP_SIGNUP_PATH}',
                params={'redirect_url': '/dashboard'},
                follow_redirects=False,
            )
        assert 'redirect_url=%2Fdashboard' in response.headers['location']


# ── POST /oauth/idp/signup ──────────────────────────────────────────────


class TestIdpSignup:
    def test_404_when_unavailable(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 404

    def test_password_mismatch_redirects_with_error(self, client):
        with _available(has_password_super_admin=False):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'different123',
                },
                follow_redirects=False,
            )
        assert response.status_code == 302
        location = response.headers['location']
        assert IDP_SIGNUP_PATH in location
        query = parse_qs(urlparse(location).query)
        assert query['error'] == ['password_mismatch']

    def test_password_too_short_redirects_with_error(self, client):
        with _available(has_password_super_admin=False):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'short',
                    'confirm_password': 'short',
                },
                follow_redirects=False,
            )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['password_too_short']

    def test_email_taken_redirects_with_error(self, client):
        existing = _mock_user(email='dev@example.com', password_hash='existing_hash')
        with (
            _available(has_password_super_admin=False),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=existing,
            ),
        ):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
                follow_redirects=False,
            )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['email_taken']

    def test_claims_existing_passwordless_account(self, client):
        """Sign-up on an existing user with ``password_hash=None`` sets the
        password and completes login instead of rejecting with ``email_taken``
        or creating a duplicate account. Covers both a plain
        OAuth-provisioned user and a super admin who already has the role
        but was never given a password (e.g. backfilled by migration 138) —
        ``idp`` treats them identically; only ``UserStore
        .has_super_admin_with_password()`` cares about the role.
        """
        user_id = derive_idp_user_id('dev@example.com')
        existing = _mock_user(
            user_id=user_id, email='dev@example.com', password_hash=None
        )

        with (
            _available(has_password_super_admin=False),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=existing,
            ),
            patch(
                'server.routes.idp._set_password_hash',
                new_callable=AsyncMock,
            ) as mock_set_hash,
            patch(
                'server.routes.idp.UserStore.grant_super_admin',
                new_callable=AsyncMock,
                return_value=existing,
            ) as mock_grant,
            patch(
                'server.routes.idp.UserStore.create_user',
                new_callable=AsyncMock,
            ) as mock_create,
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
                follow_redirects=False,
            )

        # Password was set and the super-admin role granted on the existing
        # user, no new user created.
        mock_set_hash.assert_awaited_once()
        assert mock_set_hash.call_args.args[0] == str(existing.id)
        mock_grant.assert_awaited_once_with(str(existing.id))
        mock_create.assert_not_awaited()
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args.kwargs['is_new_user'] is False
        assert mock_complete.call_args.kwargs['user'] is existing

    def test_claim_grants_super_admin_when_existing_user_lacks_role(self, client):
        """Claiming an existing passwordless *non-admin* account (e.g. a
        plain OAuth-provisioned user becoming the first admin) grants the
        super-admin role as part of completing the bootstrap, not just the
        password."""
        existing = _mock_user(email='dev@example.com', password_hash=None)
        granted = _mock_user(email='dev@example.com', password_hash=None)

        with (
            _available(has_password_super_admin=False),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=existing,
            ),
            patch(
                'server.routes.idp._set_password_hash',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.idp.UserStore.grant_super_admin',
                new_callable=AsyncMock,
                return_value=granted,
            ) as mock_grant,
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
                follow_redirects=False,
            )

        mock_grant.assert_awaited_once_with(str(existing.id))
        mock_complete.assert_awaited_once()
        # The (now super-admin) user returned by grant_super_admin is what
        # gets passed through to complete the login, not the stale `existing`.
        assert mock_complete.call_args.kwargs['user'] is granted

    def test_claim_grant_failure_returns_500(self, client):
        """If granting the role on the claimed account fails (e.g. the user
        was deleted concurrently), fail loudly instead of silently logging
        someone in without the super-admin role this flow promises."""
        existing = _mock_user(email='dev@example.com', password_hash=None)

        with (
            _available(has_password_super_admin=False),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=existing,
            ),
            patch(
                'server.routes.idp._set_password_hash',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.idp.UserStore.grant_super_admin',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 500

    def test_creates_user_and_hashes_password(self, client):
        user_id = derive_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)

        with (
            _available(has_password_super_admin=False),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.idp.UserStore.create_user',
                new_callable=AsyncMock,
                return_value=mock_user,
            ) as mock_create,
            patch(
                'server.routes.idp._set_password_hash',
                new_callable=AsyncMock,
            ) as mock_set_hash,
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
                follow_redirects=False,
            )

        mock_create.assert_awaited_once()
        assert mock_create.call_args.args[0] == user_id
        mock_set_hash.assert_awaited_once()
        assert mock_set_hash.call_args.args[0] == user_id
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args.kwargs['is_new_user'] is True
        assert mock_complete.call_args.kwargs['user'] is mock_user

    def test_create_user_failure_returns_500(self, client):
        with (
            _available(has_password_super_admin=False),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.idp.UserStore.create_user',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 500

    def test_blocked_when_password_super_admin_already_exists(self, client):
        """Sign-up is bootstrap-only: once an admin can already log in with a
        password, POSTing to sign-up (even with valid input) is rejected —
        redirected to login with an explanatory error, instead of creating
        another account."""
        with _available(has_password_super_admin=True):
            response = client.post(
                f'/oauth/{IDP_SIGNUP_PATH}',
                data={
                    'email': 'someone-else@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
                follow_redirects=False,
            )
        assert response.status_code == 302
        location = response.headers['location']
        assert IDP_LOGIN_PATH in location
        query = parse_qs(urlparse(location).query)
        assert query['error'] == ['superadmin_exists']


# ── POST /oauth/idp/login ───────────────────────────────────────────────


class TestIdpLogin:
    def test_404_when_unavailable(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'password123'},
            )
        assert response.status_code == 404

    def test_redirects_to_signup_when_no_password_super_admin(self, client):
        """No admin can log in with a password yet — login can't succeed for
        anyone, so the bootstrap (sign-up) form is where the request is sent
        instead."""
        with _available(has_password_super_admin=False):
            response = client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'password123'},
                follow_redirects=False,
            )
        assert response.status_code == 302
        assert f'/oauth/{IDP_SIGNUP_PATH}' in response.headers['location']

    def test_unknown_email_redirects_with_error(self, client):
        with (
            _available(),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'password123'},
                follow_redirects=False,
            )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['invalid_credentials']

    def test_wrong_password_redirects_with_error(self, client):
        mock_user = _mock_user(password_hash=hash_password('correct-password'))
        with (
            _available(),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'wrong-password'},
                follow_redirects=False,
            )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['invalid_credentials']

    def test_no_password_set_redirects_with_error(self, client):
        """A user with no ``password_hash`` (e.g. legacy/real-IDP row) can't
        log in via the dev IDP, regardless of what password is guessed."""
        mock_user = _mock_user(password_hash=None)
        with (
            _available(),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'anything'},
                follow_redirects=False,
            )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['invalid_credentials']

    def test_correct_password_logs_in(self, client):
        mock_user = _mock_user(password_hash=hash_password('correct-password'))
        with (
            _available(),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            response = client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'correct-password'},
                follow_redirects=False,
            )
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args.kwargs['is_new_user'] is False
        assert mock_complete.call_args.kwargs['user'] is mock_user
        assert response.status_code == 302

    def test_falls_back_to_email_lookup(self, client):
        """A user found by email (not the derived id) can still log in."""
        mock_user = _mock_user(password_hash=hash_password('correct-password'))
        with (
            _available(),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            client.post(
                f'/oauth/{IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'correct-password'},
                follow_redirects=False,
            )
        mock_complete.assert_awaited_once()


# ── _complete_idp_login — shared post-auth steps ──────────────────────


class TestCompleteIdpLogin:
    @pytest.mark.asyncio
    async def test_tos_auto_accepted_for_new_user(self):
        user_id = derive_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)
        request = MagicMock()

        with (
            patch(
                'server.routes.idp._accept_tos_for_idp_user',
                new_callable=AsyncMock,
            ) as mock_accept_tos,
            patch(
                'server.routes.idp.UserStore.record_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.idp.DefaultOrgBootstrapService.apply_for_user',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                'server.routes.idp._track_idp_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.idp._should_redirect_to_onboarding_idp_user',
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch('server.routes.idp._set_idp_cookie'),
            patch(
                'server.routes.idp.get_web_url',
                return_value='http://testserver',
            ),
        ):
            response = await idp._complete_idp_login(
                request=request,
                user=mock_user,
                is_new_user=True,
                email='dev@example.com',
                redirect_url='',
            )
        mock_accept_tos.assert_awaited_once_with(user_id)
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_tos_skip_if_already_accepted(self):
        from datetime import datetime, timezone

        user_id = derive_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=datetime.now(timezone.utc))
        request = MagicMock()

        with (
            patch(
                'server.routes.idp._accept_tos_for_idp_user',
                new_callable=AsyncMock,
            ) as mock_accept_tos,
            patch(
                'server.routes.idp.UserStore.record_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.idp._track_idp_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.idp._should_redirect_to_onboarding_idp_user',
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch('server.routes.idp._set_idp_cookie'),
            patch(
                'server.routes.idp.get_web_url',
                return_value='http://testserver',
            ),
        ):
            await idp._complete_idp_login(
                request=request,
                user=mock_user,
                is_new_user=False,
                email='dev@example.com',
                redirect_url='',
            )
        mock_accept_tos.assert_not_called()


# ── status endpoint ───────────────────────────────────────────────────────


class TestIdpStatus:
    def test_returns_enabled_true(self, client):
        with _available():
            response = client.get('/api/idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is True

    def test_returns_enabled_false_on_cloud(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.get('/api/idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is False


# ── has-password endpoint ───────────────────────────────────────────────


class TestIdpHasPassword:
    def test_404_when_idp_disabled(self, app, client):
        with (
            patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False),
            _authenticated_as(app, 'user-1'),
        ):
            response = client.get('/api/idp/password')
        assert response.status_code == 404

    def test_401_when_not_authenticated(self, app, client):
        with _available(), _authenticated_as(app, None):
            response = client.get('/api/idp/password')
        assert response.status_code == 401

    def test_true_when_password_hash_set(self, app, client):
        mock_user = _mock_user(password_hash=hash_password('secret123'))
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = client.get('/api/idp/password')
        assert response.status_code == 200
        assert response.json() == {'has_password': True}

    def test_false_when_no_password_hash(self, app, client):
        mock_user = _mock_user(password_hash=None)
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = client.get('/api/idp/password')
        assert response.status_code == 200
        assert response.json() == {'has_password': False}

    def test_false_when_user_not_found(self, app, client):
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.get('/api/idp/password')
        assert response.status_code == 200
        assert response.json() == {'has_password': False}


# ── set-password endpoint ───────────────────────────────────────────────


class TestIdpSetPassword:
    @staticmethod
    def _post(client, **body):
        payload = {
            'new_password': 'new-password123',
            'confirm_password': 'new-password123',
            **body,
        }
        return client.post('/api/idp/password', json=payload)

    def test_404_when_idp_disabled(self, app, client):
        with (
            patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False),
            _authenticated_as(app, 'user-1'),
        ):
            response = self._post(client)
        assert response.status_code == 404

    def test_401_when_not_authenticated(self, app, client):
        with _available(), _authenticated_as(app, None):
            response = self._post(client)
        assert response.status_code == 401

    def test_400_when_passwords_do_not_match(self, app, client):
        with _available(), _authenticated_as(app, 'user-1'):
            response = self._post(
                client,
                new_password='new-password123',
                confirm_password='different123',
            )
        assert response.status_code == 400
        assert 'match' in response.json()['detail']

    def test_400_when_new_password_too_short(self, app, client):
        with _available(), _authenticated_as(app, 'user-1'):
            response = self._post(
                client, new_password='short', confirm_password='short'
            )
        assert response.status_code == 400
        assert 'at least' in response.json()['detail']

    def test_404_when_user_not_found(self, app, client):
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = self._post(client)
        assert response.status_code == 404

    def test_400_when_current_password_missing_but_required(self, app, client):
        """A user with an existing password must supply the correct current
        one — omitting it is rejected, not treated as "no password yet"."""
        mock_user = _mock_user(password_hash=hash_password('correct-password'))
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = self._post(client)
        assert response.status_code == 400
        assert 'incorrect' in response.json()['detail'].lower()

    def test_400_when_current_password_wrong(self, app, client):
        mock_user = _mock_user(password_hash=hash_password('correct-password'))
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = self._post(client, current_password='wrong-password')
        assert response.status_code == 400
        assert 'incorrect' in response.json()['detail'].lower()

    def test_sets_password_when_none_exists(self, app, client):
        """First-time password setup — current_password is not required when
        the user has no ``password_hash`` yet (e.g. an OAuth-only account)."""
        mock_user = _mock_user(password_hash=None)
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                'server.routes.idp._set_password_hash',
                new_callable=AsyncMock,
            ) as mock_set_hash,
        ):
            response = self._post(client)
        assert response.status_code == 200
        mock_set_hash.assert_awaited_once()
        assert mock_set_hash.call_args.args[0] == 'user-1'

    def test_changes_password_with_correct_current_password(self, app, client):
        mock_user = _mock_user(password_hash=hash_password('correct-password'))
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                'server.routes.idp._set_password_hash',
                new_callable=AsyncMock,
            ) as mock_set_hash,
        ):
            response = self._post(client, current_password='correct-password')
        assert response.status_code == 200
        mock_set_hash.assert_awaited_once()

    def test_rate_limited(self, app, client):
        mock_user = _mock_user(password_hash=None)
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.routes.idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                'server.routes.idp.check_rate_limit_by_user_id',
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=429, detail='Too many requests'),
            ),
        ):
            response = self._post(client)
        assert response.status_code == 429


# ── sign-up link token helpers ──────────────────────────────────────────


class TestSignupLinkToken:
    def test_roundtrip(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            email = idp._verify_signup_link_token(token)
        assert email == 'invitee@example.com'

    def test_lowercases_email_on_create(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('Invitee@Example.COM')
            email = idp._verify_signup_link_token(token)
        assert email == 'invitee@example.com'

    def test_expires_after_configured_hours(self, jwt_svc):
        from datetime import datetime, timezone

        from freezegun import freeze_time

        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        with freeze_time(start) as frozen:
            with _patch_jwt_service(jwt_svc):
                token = idp._create_signup_link_token('invitee@example.com')

                frozen.move_to(
                    start + timedelta(hours=SIGNUP_LINK_EXPIRY_HOURS, seconds=1)
                )
                with pytest.raises(ValueError):
                    idp._verify_signup_link_token(token)

    def test_still_valid_just_before_expiry(self, jwt_svc):
        from datetime import datetime, timezone

        from freezegun import freeze_time

        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        with freeze_time(start) as frozen:
            with _patch_jwt_service(jwt_svc):
                token = idp._create_signup_link_token('invitee@example.com')

                frozen.move_to(
                    start + timedelta(hours=SIGNUP_LINK_EXPIRY_HOURS, seconds=-1)
                )
                email = idp._verify_signup_link_token(token)
        assert email == 'invitee@example.com'

    def test_rejects_token_with_wrong_purpose(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            other_token = jwt_svc.create_jws_token(
                {'purpose': 'something_else', 'email': 'invitee@example.com'}
            )
            with pytest.raises(ValueError):
                idp._verify_signup_link_token(other_token)

    def test_rejects_malformed_token(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            with pytest.raises(ValueError):
                idp._verify_signup_link_token('not-a-jwt')

    def test_rejects_tampered_token(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
        tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')
        with _patch_jwt_service(jwt_svc), pytest.raises(ValueError):
            idp._verify_signup_link_token(tampered)


# ── GET /oauth/idp/invite ────────────────────────────────────────────────


class TestIdpInviteForm:
    def test_404_when_unavailable(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.get(f'/oauth/{IDP_INVITE_PATH}', params={'token': token})
        assert response.status_code == 404

    def test_renders_form_with_valid_token(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
                response = client.get(
                    f'/oauth/{IDP_INVITE_PATH}', params={'token': token}
                )
        assert response.status_code == 200
        assert 'invitee@example.com' in response.text
        assert 'name="token"' in response.text

    def test_400_on_invalid_token(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
            response = client.get(
                f'/oauth/{IDP_INVITE_PATH}', params={'token': 'garbage'}
            )
        assert response.status_code == 400

    def test_400_on_expired_token(self, client, jwt_svc):
        from datetime import datetime, timezone

        from freezegun import freeze_time

        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        with freeze_time(start) as frozen:
            with _patch_jwt_service(jwt_svc):
                token = idp._create_signup_link_token('invitee@example.com')

            frozen.move_to(start + timedelta(hours=SIGNUP_LINK_EXPIRY_HOURS, seconds=1))
            with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
                response = client.get(
                    f'/oauth/{IDP_INVITE_PATH}', params={'token': token}
                )
        assert response.status_code == 400


# ── POST /oauth/idp/invite ───────────────────────────────────────────────


class TestIdpInviteAccept:
    def test_404_when_unavailable(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False):
            response = client.post(
                f'/oauth/{IDP_INVITE_PATH}',
                data={
                    'token': token,
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 404

    def test_400_on_invalid_token(self, client):
        with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
            response = client.post(
                f'/oauth/{IDP_INVITE_PATH}',
                data={
                    'token': 'garbage',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 400

    def test_password_mismatch_redirects_with_error(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
                response = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'different123',
                    },
                    follow_redirects=False,
                )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['password_mismatch']
        assert query['token'] == [token]

    def test_password_too_short_redirects_with_error(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True):
                response = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'short',
                        'confirm_password': 'short',
                    },
                    follow_redirects=False,
                )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['password_too_short']

    def test_link_already_used_redirects_with_error(self, client, jwt_svc):
        """Replaying a link whose account already has a password is
        rejected instead of silently overwriting the password."""
        existing = _mock_user(
            email='invitee@example.com', password_hash='existing_hash'
        )
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True),
                patch(
                    'server.routes.idp.UserStore.get_user_by_id',
                    new_callable=AsyncMock,
                    return_value=existing,
                ),
            ):
                response = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                    follow_redirects=False,
                )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers['location']).query)
        assert query['error'] == ['link_used']

    def test_claims_existing_passwordless_account(self, client, jwt_svc):
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True),
                patch(
                    'server.routes.idp.UserStore.get_user_by_id',
                    new_callable=AsyncMock,
                    return_value=existing,
                ),
                patch(
                    'server.routes.idp._set_password_hash',
                    new_callable=AsyncMock,
                ) as mock_set_hash,
                patch(
                    'server.routes.idp.UserStore.create_user',
                    new_callable=AsyncMock,
                ) as mock_create,
                _patch_complete_login() as mock_complete,
            ):
                mock_complete.return_value = RedirectResponse('/', status_code=302)
                client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                    follow_redirects=False,
                )

        mock_set_hash.assert_awaited_once()
        assert mock_set_hash.call_args.args[0] == str(existing.id)
        mock_create.assert_not_awaited()
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args.kwargs['is_new_user'] is False
        assert mock_complete.call_args.kwargs['user'] is existing

    def test_creates_user_and_hashes_password(self, client, jwt_svc):
        user_id = derive_idp_user_id('invitee@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True),
                patch(
                    'server.routes.idp.UserStore.get_user_by_id',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.UserStore.get_user_by_email',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.UserStore.create_user',
                    new_callable=AsyncMock,
                    return_value=mock_user,
                ) as mock_create,
                patch(
                    'server.routes.idp._set_password_hash',
                    new_callable=AsyncMock,
                ) as mock_set_hash,
                _patch_complete_login() as mock_complete,
            ):
                mock_complete.return_value = RedirectResponse('/', status_code=302)
                client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                    follow_redirects=False,
                )

        mock_create.assert_awaited_once()
        assert mock_create.call_args.args[0] == user_id
        mock_set_hash.assert_awaited_once()
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args.kwargs['is_new_user'] is True
        assert mock_complete.call_args.kwargs['user'] is mock_user
        # Does not grant the super-admin role -- ordinary invited user.
        assert mock_complete.call_args.kwargs['email'] == 'invitee@example.com'

    def test_create_user_failure_returns_500(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch('server.routes.idp.ENABLE_INTEGRATED_IDP', True),
                patch(
                    'server.routes.idp.UserStore.get_user_by_id',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.UserStore.get_user_by_email',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.UserStore.create_user',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
            ):
                response = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                )
        assert response.status_code == 500


# ── POST /api/idp/signup-links (admin minting endpoint) ─────────────────


class TestCreateSignupLink:
    def test_404_when_idp_disabled(self, app, client):
        with (
            patch('server.routes.idp.ENABLE_INTEGRATED_IDP', False),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
        ):
            response = client.post(
                '/api/idp/signup-links', json={'email': 'invitee@example.com'}
            )
        assert response.status_code == 404

    def test_401_when_not_authenticated(self, app, client):
        with _available(), _authenticated_as(app, None):
            response = client.post(
                '/api/idp/signup-links', json={'email': 'invitee@example.com'}
            )
        assert response.status_code == 401

    def test_403_when_not_super_admin(self, app, client):
        with (
            _available(),
            _authenticated_as(app, 'user-1'),
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.auth.authorization.get_user_super_role',
                AsyncMock(return_value=None),
            ),
        ):
            response = client.post(
                '/api/idp/signup-links', json={'email': 'invitee@example.com'}
            )
        assert response.status_code == 403

    def test_mints_link_for_super_admin(self, app, client, jwt_svc):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
        ):
            response = client.post(
                '/api/idp/signup-links', json={'email': 'Invitee@Example.COM'}
            )
        assert response.status_code == 201
        body = response.json()
        assert IDP_INVITE_PATH in body['url']
        assert 'token=' in body['url']
        assert 'expires_at' in body

        # The minted token embeds the lower-cased email and is itself
        # verifiable by the accept endpoint's helper.
        query = parse_qs(urlparse(body['url']).query)
        token = query['token'][0]
        with _patch_jwt_service(jwt_svc):
            assert idp._verify_signup_link_token(token) == 'invitee@example.com'

    def test_rate_limited(self, app, client):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            patch(
                'server.routes.idp.check_rate_limit_by_user_id',
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=429, detail='Too many requests'),
            ),
        ):
            response = client.post(
                '/api/idp/signup-links', json={'email': 'invitee@example.com'}
            )
        assert response.status_code == 429
