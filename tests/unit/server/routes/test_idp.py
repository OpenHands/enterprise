"""Tests for the dev IDP module — an email+password login that plugs into
the OAuth v2 flow as a real ``oauth_providers`` row (OHE-3381).

These tests exercise:

* ``derive_idp_user_id`` — deterministic, case-insensitive UUID derivation
* ``is_idp_available`` / ``_get_integrated_idp_provider`` — gating logic
  (whether the integrated IDP's ``oauth_providers`` row exists; independent
  of whether a real IDP is also configured)
* ``GET /oauth/idp-login`` — redirects to whichever IDP ``get_first_idp``
  (most-recently-created) resolves to
* ``GET /oauth/{provider_id}/login`` — for the integrated IDP's row,
  redirects to the dedicated email+password pages instead of starting an
  OAuth flow
* ``GET /oauth/idp/login`` / ``GET /oauth/idp/signup`` — serve the
  HTML forms
* ``POST /oauth/idp/signup`` — creates an account, hashes the password,
  completes the login
* ``POST /oauth/idp/login`` — verifies the password, completes the login
* Error cases — 404 when unavailable, invalid credentials, taken email,
  password validation
"""

from __future__ import annotations

import uuid
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
from server.auth.password_hashing import hash_password, verify_password
from server.routes import idp
from server.routes.idp import (
    IDP_INVITE_PATH,
    IDP_LOGIN_PATH,
    IDP_SIGNUP_PATH,
    SIGNUP_LINK_EXPIRY_HOURS,
    derive_idp_user_id,
    is_idp_available,
)
from storage.oauth_provider import INTEGRATED_IDP_CATEGORY

# Fixed id used in route-dispatch tests (``/oauth/{id}/login`` etc.) that
# exercise the integrated IDP through ``oauth_v2.py``'s generic
# provider-id-keyed routes. Not a sentinel the app recognizes specially —
# just a stand-in DB id that ``_available()`` wires ``OAuthProviderStore``
# lookups to resolve to the fake integrated-idp row below.
_TEST_IDP_PROVIDER_ID = 999001


def _fake_integrated_idp_provider(provider_id: int = _TEST_IDP_PROVIDER_ID):
    """A ``MagicMock`` standing in for the integrated IDP's real DB row."""
    provider = MagicMock()
    provider.id = provider_id
    provider.provider_category = INTEGRATED_IDP_CATEGORY
    provider.is_idp = True
    provider.token_url = None
    provider.display_name = 'Password Login'
    return provider


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
    user.is_disabled = False
    user.deletion_pending = False
    user.password_hash = password_hash
    return user


@contextmanager
def _available(
    has_password_super_admin: bool = True,
    provider_id: int = _TEST_IDP_PROVIDER_ID,
):
    """Make the integrated IDP available (its ``oauth_providers`` row exists).

    Patches ``_get_integrated_idp_provider`` (the single source of truth for
    availability and the row's id, used by both ``idp.py`` and, through
    ``is_idp_available``, the web-client config) *and*
    ``OAuthProviderStore.get_by_id`` (used by ``oauth_v2.py``'s generic
    ``/oauth/{id}/...`` routes) so both halves of the integrated-IDP
    dispatch agree on the same fake row.

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
    fake_provider = _fake_integrated_idp_provider(provider_id)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new_callable=AsyncMock,
                return_value=fake_provider,
            )
        )
        stack.enter_context(
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_by_id',
                new_callable=AsyncMock,
                return_value=fake_provider,
            )
        )
        stack.enter_context(
            patch(
                'server.routes.idp.UserStore.has_super_admin_with_password',
                new_callable=AsyncMock,
                return_value=has_password_super_admin,
            )
        )
        yield fake_provider


@contextmanager
def _unavailable():
    """Make the integrated IDP unavailable (no ``oauth_providers`` row)."""
    with patch(
        'server.routes.idp._get_integrated_idp_provider',
        new_callable=AsyncMock,
        return_value=None,
    ):
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


# ── is_idp_available / _get_integrated_idp_provider ─────────────────────


class TestIsIdpAvailable:
    def test_disabled_when_no_row(self):
        with patch(
            'server.routes.idp.OAuthProviderStore.get_first_by_category',
            new_callable=AsyncMock,
            return_value=None,
        ):
            import asyncio

            result = asyncio.run(is_idp_available())
        assert result is False

    def test_enabled_when_row_exists(self):
        with patch(
            'server.routes.idp.OAuthProviderStore.get_first_by_category',
            new_callable=AsyncMock,
            return_value=_fake_integrated_idp_provider(),
        ):
            import asyncio

            result = asyncio.run(is_idp_available())
        assert result is True

    def test_queries_integrated_idp_category(self):
        """``is_idp_available`` looks up the row by
        ``INTEGRATED_IDP_CATEGORY`` specifically -- not just "any IDP"."""
        with patch(
            'server.routes.idp.OAuthProviderStore.get_first_by_category',
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_get:
            import asyncio

            asyncio.run(is_idp_available())
        mock_get.assert_awaited_once_with(INTEGRATED_IDP_CATEGORY)

    def test_enabled_even_when_real_idp_configured(self):
        """Availability is governed solely by whether the integrated IDP's
        own row exists -- a configured real IDP does not affect it (and,
        per ``get_first_idp``'s most-recent-first priority, does not
        necessarily take priority over it for ``/oauth/idp-login`` either;
        see ``storage.oauth_provider_store``)."""
        with (
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
            ),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            import asyncio

            result = asyncio.run(is_idp_available())
        assert result is True


# ── _get_integrated_idp_provider ─────────────────────────────────────────


class TestGetIntegratedIdpProvider:
    def test_returns_row_when_available(self):
        fake = _fake_integrated_idp_provider()
        with patch(
            'server.routes.idp.OAuthProviderStore.get_first_by_category',
            new_callable=AsyncMock,
            return_value=fake,
        ):
            import asyncio

            result = asyncio.run(idp._get_integrated_idp_provider())
        assert result is fake

    def test_returns_none_without_row(self):
        with patch(
            'server.routes.idp.OAuthProviderStore.get_first_by_category',
            new_callable=AsyncMock,
            return_value=None,
        ):
            import asyncio

            result = asyncio.run(idp._get_integrated_idp_provider())
        assert result is None

    def test_returns_row_even_when_real_idp_configured(self):
        with (
            patch(
                'server.routes.idp.OAuthProviderStore.get_first_by_category',
                new_callable=AsyncMock,
                return_value=_fake_integrated_idp_provider(),
            ),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            import asyncio

            result = asyncio.run(idp._get_integrated_idp_provider())
        assert result is not None


# ── GET /oauth/idp-login redirect ─────────────────────────────────────────


class TestIdpLoginRedirect:
    def test_redirects_to_idp_provider(self, client):
        """``/oauth/idp-login`` redirects to ``/oauth/{id}/login`` for
        whichever provider ``get_first_idp`` resolves to -- here, the
        integrated IDP's row."""
        fake = _fake_integrated_idp_provider()
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
            new_callable=AsyncMock,
            return_value=fake,
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 302
        assert f'/oauth/{fake.id}/login' in response.headers['location']

    def test_returns_404_when_no_idp_configured(self, client):
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 404


# ── GET /oauth/{id}/login redirects to the dedicated pages for the ──────
# ── integrated IDP's row ─────────────────────────────────────────────────


class TestOAuthV2LoginRedirectsIdp:
    def test_redirects_to_idp_login_page(self, client):
        with _available():
            response = client.get(
                f'/oauth/{_TEST_IDP_PROVIDER_ID}/login', follow_redirects=False
            )
        assert response.status_code == 302
        assert f'/oauth/{IDP_LOGIN_PATH}' in response.headers['location']

    def test_forwards_redirect_url(self, client):
        with _available():
            response = client.get(
                f'/oauth/{_TEST_IDP_PROVIDER_ID}/login',
                params={'redirect_url': '/dashboard'},
                follow_redirects=False,
            )
        location = response.headers['location']
        assert 'redirect_url=%2Fdashboard' in location

    def test_404_when_no_such_provider(self, client):
        """Not an "integrated IDP disabled" case anymore -- just the
        standard ``_get_provider`` 404 for an id with no row at all."""
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_by_id',
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get(
                f'/oauth/{_TEST_IDP_PROVIDER_ID}/login', follow_redirects=False
            )
        assert response.status_code == 404


# ── GET /oauth/{id}/callback 404s for the integrated IDP's row ──────────


class TestOAuthV2CallbackRejectsIdp:
    def test_404_for_idp_provider(self, client):
        """404s on provider category alone, before any ``code``/``state``
        validation -- a GET callback for the integrated/password IDP is
        always wrong, regardless of what query params a caller sends."""
        with _available():
            response = client.get(
                f'/oauth/{_TEST_IDP_PROVIDER_ID}/callback',
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
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
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
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
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
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
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
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
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
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
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
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
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
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
            response = client.get('/api/idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is False


# ── has-password endpoint ───────────────────────────────────────────────


class TestIdpHasPassword:
    def test_404_when_idp_disabled(self, app, client):
        with (
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=None),
            ),
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
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=None),
            ),
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
            link = idp._verify_signup_link_token(token)
        assert link.email == 'invitee@example.com'
        assert link.org_id is None

    def test_lowercases_email_on_create(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('Invitee@Example.COM')
            link = idp._verify_signup_link_token(token)
        assert link.email == 'invitee@example.com'

    def test_roundtrip_with_org_id(self, jwt_svc):
        org_id = uuid.uuid4()
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com', org_id=org_id)
            link = idp._verify_signup_link_token(token)
        assert link.email == 'invitee@example.com'
        assert link.org_id == org_id

    def test_role_defaults_to_member(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            link = idp._verify_signup_link_token(token)
        assert link.role == 'member'

    def test_roundtrip_with_explicit_role(self, jwt_svc):
        org_id = uuid.uuid4()
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token(
                'invitee@example.com', org_id=org_id, role='admin'
            )
            link = idp._verify_signup_link_token(token)
        assert link.role == 'admin'
        assert link.org_id == org_id

    def test_roundtrip_with_superadmin_role(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token(
                'invitee@example.com', role='superadmin'
            )
            link = idp._verify_signup_link_token(token)
        assert link.role == 'superadmin'
        assert link.org_id is None

    def test_missing_role_claim_defaults_to_member(self, jwt_svc):
        """Links minted before the ``role`` claim existed keep working."""
        with _patch_jwt_service(jwt_svc):
            token = jwt_svc.create_jws_token(
                {
                    'purpose': idp._SIGNUP_LINK_PURPOSE,
                    'email': 'invitee@example.com',
                }
            )
            link = idp._verify_signup_link_token(token)
        assert link.role == 'member'

    def test_rejects_unrecognized_role(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = jwt_svc.create_jws_token(
                {
                    'purpose': idp._SIGNUP_LINK_PURPOSE,
                    'email': 'invitee@example.com',
                    'role': 'not-a-real-role',
                }
            )
            with pytest.raises(ValueError):
                idp._verify_signup_link_token(token)

    def test_rejects_token_with_malformed_org_id(self, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = jwt_svc.create_jws_token(
                {
                    'purpose': idp._SIGNUP_LINK_PURPOSE,
                    'email': 'invitee@example.com',
                    'org_id': 'not-a-uuid',
                }
            )
            with pytest.raises(ValueError):
                idp._verify_signup_link_token(token)

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
                link = idp._verify_signup_link_token(token)
        assert link.email == 'invitee@example.com'

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
        # Flip a character in the middle of the signature rather than the
        # very last one: a trailing base64 group can be partially padded, so
        # flipping its last character sometimes leaves the decoded bytes (and
        # therefore the signature) unchanged -- a flaky false negative. A
        # middle character always sits in a complete 4-char/3-byte group, so
        # changing it is guaranteed to change the decoded signature bytes.
        header, body, signature = token.split('.')
        mid = len(signature) // 2
        flipped_char = 'a' if signature[mid] != 'a' else 'b'
        tampered_signature = signature[:mid] + flipped_char + signature[mid + 1 :]
        tampered = f'{header}.{body}.{tampered_signature}'
        with _patch_jwt_service(jwt_svc), pytest.raises(ValueError):
            idp._verify_signup_link_token(tampered)


# ── GET /oauth/idp/invite ────────────────────────────────────────────────


class TestIdpInviteForm:
    def test_404_when_unavailable(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
            response = client.get(f'/oauth/{IDP_INVITE_PATH}', params={'token': token})
        assert response.status_code == 404

    def test_renders_form_with_valid_token(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
            ):
                response = client.get(
                    f'/oauth/{IDP_INVITE_PATH}', params={'token': token}
                )
        assert response.status_code == 200
        assert 'invitee@example.com' in response.text
        assert 'name="token"' in response.text
        # The email is shown as plain text, not an editable form field --
        # it's sourced solely from the verified token (see
        # idp_invite_accept, which doesn't even accept an `email` field).
        assert 'name="email"' not in response.text
        assert '<p class="value-display">invitee@example.com</p>' in response.text

    def test_400_on_invalid_token(self, client):
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=_fake_integrated_idp_provider()),
        ):
            response = client.get(
                f'/oauth/{IDP_INVITE_PATH}', params={'token': 'garbage'}
            )
        assert response.status_code == 400

    def test_escapes_email_in_rendered_html(self, client, jwt_svc):
        """Defense in depth: even though ``EmailStr`` validation on the
        minting endpoint should reject HTML-special characters, the email
        is still HTML-escaped before being rendered as page text."""
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with patch(
                'server.routes.idp._verify_signup_link_token',
                return_value=idp.SignupLinkPayload(
                    email='<script>alert(1)</script>@example.com',
                    org_id=None,
                    role='member',
                ),
            ):
                with patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ):
                    response = client.get(
                        f'/oauth/{IDP_INVITE_PATH}', params={'token': token}
                    )
        assert response.status_code == 200
        assert '<script>' not in response.text
        assert '&lt;script&gt;' in response.text

    def test_400_on_expired_token(self, client, jwt_svc):
        from datetime import datetime, timezone

        from freezegun import freeze_time

        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        with freeze_time(start) as frozen:
            with _patch_jwt_service(jwt_svc):
                token = idp._create_signup_link_token('invitee@example.com')

            frozen.move_to(start + timedelta(hours=SIGNUP_LINK_EXPIRY_HOURS, seconds=1))
            with patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
            ):
                response = client.get(
                    f'/oauth/{IDP_INVITE_PATH}', params={'token': token}
                )
        assert response.status_code == 400


# ── POST /oauth/idp/invite ───────────────────────────────────────────────


class TestIdpInviteAccept:
    def test_404_when_unavailable(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=None),
        ):
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
        with patch(
            'server.routes.idp._get_integrated_idp_provider',
            new=AsyncMock(return_value=_fake_integrated_idp_provider()),
        ):
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
            with patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
            ):
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
            with patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=_fake_integrated_idp_provider()),
            ):
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

    def test_resets_existing_password(self, client, jwt_svc):
        """A link for an email whose account already has a password resets
        it (and logs in as that user) rather than refusing -- this is the
        only password-reset mechanism this IDP has, and minting the link is
        itself the admin action that authorizes the reset."""
        existing = _mock_user(
            email='invitee@example.com', password_hash='existing_hash'
        )
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                        'password': 'new-password123',
                        'confirm_password': 'new-password123',
                    },
                    follow_redirects=False,
                )

        mock_set_hash.assert_awaited_once()
        assert mock_set_hash.call_args.args[0] == str(existing.id)
        mock_create.assert_not_awaited()
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args.kwargs['is_new_user'] is False
        assert mock_complete.call_args.kwargs['user'] is existing

    def test_claims_existing_passwordless_account(self, client, jwt_svc):
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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

    def test_replay_resets_password_again(self, client, jwt_svc):
        """A genuine end-to-end replay: use the same token twice. The first
        POST creates the account and sets its password; a second POST with
        the identical token -- still within its 72-hour validity window --
        succeeds too, resetting the password a second time rather than
        being refused. There's no separate "used" tracking: the link is
        valid, and reusable, for its whole expiry window (see the
        module-level "admin-issued sign-up links" design notes)."""
        user_id = derive_idp_user_id('invitee@example.com')
        created_user = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )

        async def fake_get_user_by_id(_user_id):
            return created_user if created_user.password_hash is not None else None

        async def fake_set_password_hash(_user_id, hashed):
            created_user.password_hash = hashed

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
                patch(
                    'server.routes.idp.UserStore.get_user_by_id',
                    side_effect=fake_get_user_by_id,
                ),
                patch(
                    'server.routes.idp.UserStore.get_user_by_email',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.UserStore.create_user',
                    new_callable=AsyncMock,
                    return_value=created_user,
                ) as mock_create,
                patch(
                    'server.routes.idp._set_password_hash',
                    side_effect=fake_set_password_hash,
                ),
                _patch_complete_login() as mock_complete,
            ):
                mock_complete.return_value = RedirectResponse('/', status_code=302)
                first = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                    follow_redirects=False,
                )
                second = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'different456',
                        'confirm_password': 'different456',
                    },
                    follow_redirects=False,
                )

        assert first.status_code == 302
        assert second.status_code == 302
        # Only the first call creates the account; the second finds and
        # resets the one the first call just created.
        mock_create.assert_awaited_once()
        assert mock_complete.await_count == 2
        assert mock_complete.call_args_list[0].kwargs['is_new_user'] is True
        assert mock_complete.call_args_list[1].kwargs['is_new_user'] is False
        # The second POST's password is the one that actually took effect.
        assert not verify_password('password123', created_user.password_hash)
        assert verify_password('different456', created_user.password_hash)

    def test_ignores_client_supplied_email_field(self, client, jwt_svc):
        """The route doesn't even declare an ``email`` Form field, so a
        client cannot override the email embedded in the token -- the
        account is always created/claimed for the token's email."""
        user_id = derive_idp_user_id('invitee@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                ),
                _patch_complete_login() as mock_complete,
            ):
                mock_complete.return_value = RedirectResponse('/', status_code=302)
                client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                        'email': 'attacker@evil.com',
                    },
                    follow_redirects=False,
                )

        mock_create.assert_awaited_once()
        assert mock_create.call_args.args[0] == user_id
        assert mock_complete.call_args.kwargs['email'] == 'invitee@example.com'

    def test_create_user_failure_returns_500(self, client, jwt_svc):
        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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


# ── org membership via admin-issued link (org_id claim) ─────────────────


class TestIdpInviteAcceptOrgMembership:
    def test_adds_user_to_org_when_not_already_a_member(self, client, jwt_svc):
        org_id = uuid.uuid4()
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )
        mock_role = MagicMock(id=7)
        mock_settings = MagicMock()
        mock_settings.agent_settings.llm.api_key.get_secret_value.return_value = (
            'sk-test'
        )

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com', org_id=org_id)
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.OrgStore.get_org_by_id',
                    new_callable=AsyncMock,
                    return_value=MagicMock(),
                ),
                patch(
                    'server.routes.idp.RoleStore.get_role_by_name',
                    new_callable=AsyncMock,
                    return_value=mock_role,
                ),
                patch(
                    'server.routes.idp.OrgService.create_litellm_integration',
                    new_callable=AsyncMock,
                    return_value=mock_settings,
                ),
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
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

        mock_add.assert_awaited_once()
        kwargs = mock_add.call_args.kwargs
        assert kwargs['org_id'] == org_id
        assert kwargs['user_id'] == existing.id
        assert kwargs['role_id'] == mock_role.id
        assert kwargs['llm_api_key'] == 'sk-test'
        assert kwargs['status'] == 'active'

    def test_skips_when_already_a_member(self, client, jwt_svc):
        """No change if the user is already in the org -- matches the
        feature's stated contract, and avoids ``add_user_to_org`` raising
        on the duplicate (org_id, user_id) primary key."""
        org_id = uuid.uuid4()
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com', org_id=org_id)
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                    return_value=MagicMock(),
                ),
                patch(
                    'server.routes.idp.OrgStore.get_org_by_id',
                    new_callable=AsyncMock,
                ) as mock_get_org,
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
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

        mock_add.assert_not_awaited()
        # Doesn't even bother looking up the org once membership is confirmed.
        mock_get_org.assert_not_awaited()

    def test_no_org_lookup_when_token_has_no_org_id(self, client, jwt_svc):
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com')
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                ) as mock_get_member,
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
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

        mock_get_member.assert_not_awaited()
        mock_add.assert_not_awaited()

    def test_org_not_found_does_not_block_login(self, client, jwt_svc):
        """If the org named in the token no longer exists by the time the
        link is used, the password is still set and the user still logs
        in -- adding them to a dangling org is simply skipped."""
        org_id = uuid.uuid4()
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com', org_id=org_id)
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.OrgStore.get_org_by_id',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
                _patch_complete_login() as mock_complete,
            ):
                mock_complete.return_value = RedirectResponse('/', status_code=302)
                response = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                    follow_redirects=False,
                )

        mock_add.assert_not_awaited()
        mock_complete.assert_awaited_once()
        assert response.status_code == 302

    def test_litellm_integration_failure_does_not_block_login(self, client, jwt_svc):
        """A failure while provisioning org access is logged and swallowed
        rather than failing the whole request -- the password was already
        set and the user should still be able to log in."""
        org_id = uuid.uuid4()
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )
        mock_role = MagicMock(id=7)

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token('invitee@example.com', org_id=org_id)
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.OrgStore.get_org_by_id',
                    new_callable=AsyncMock,
                    return_value=MagicMock(),
                ),
                patch(
                    'server.routes.idp.RoleStore.get_role_by_name',
                    new_callable=AsyncMock,
                    return_value=mock_role,
                ),
                patch(
                    'server.routes.idp.OrgService.create_litellm_integration',
                    new_callable=AsyncMock,
                    side_effect=RuntimeError('litellm down'),
                ),
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
                _patch_complete_login() as mock_complete,
            ):
                mock_complete.return_value = RedirectResponse('/', status_code=302)
                response = client.post(
                    f'/oauth/{IDP_INVITE_PATH}',
                    data={
                        'token': token,
                        'password': 'password123',
                        'confirm_password': 'password123',
                    },
                    follow_redirects=False,
                )

        mock_add.assert_not_awaited()
        mock_complete.assert_awaited_once()
        assert response.status_code == 302


# ── POST /api/idp/signup-links (admin minting endpoint) ─────────────────


class TestCreateSignupLink:
    def test_404_when_idp_disabled(self, app, client):
        with (
            patch(
                'server.routes.idp._get_integrated_idp_provider',
                new=AsyncMock(return_value=None),
            ),
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
            link = idp._verify_signup_link_token(token)
        assert link.email == 'invitee@example.com'
        assert link.org_id is None

    def test_mints_link_with_org_id(self, app, client, jwt_svc):
        org_id = uuid.uuid4()
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
            patch(
                'server.routes.idp.OrgStore.get_org_by_id',
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={'email': 'invitee@example.com', 'org_id': str(org_id)},
            )
        assert response.status_code == 201
        body = response.json()
        query = parse_qs(urlparse(body['url']).query)
        token = query['token'][0]
        with _patch_jwt_service(jwt_svc):
            link = idp._verify_signup_link_token(token)
        assert link.org_id == org_id

    def test_404_when_org_id_not_found(self, app, client, jwt_svc):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
            patch(
                'server.routes.idp.OrgStore.get_org_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={
                    'email': 'invitee@example.com',
                    'org_id': str(uuid.uuid4()),
                },
            )
        assert response.status_code == 404

    def test_mints_superadmin_link_with_no_org_id(self, app, client, jwt_svc):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={'email': 'invitee@example.com', 'role': 'superadmin'},
            )
        assert response.status_code == 201
        body = response.json()
        assert body['role'] == 'superadmin'
        query = parse_qs(urlparse(body['url']).query)
        token = query['token'][0]
        with _patch_jwt_service(jwt_svc):
            link = idp._verify_signup_link_token(token)
        assert link.role == 'superadmin'
        assert link.org_id is None

    def test_400_when_superadmin_role_combined_with_org_id(self, app, client, jwt_svc):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={
                    'email': 'invitee@example.com',
                    'role': 'superadmin',
                    'org_id': str(uuid.uuid4()),
                },
            )
        assert response.status_code == 400

    def test_400_when_admin_role_missing_org_id(self, app, client, jwt_svc):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={'email': 'invitee@example.com', 'role': 'admin'},
            )
        assert response.status_code == 400

    def test_422_when_role_unrecognized(self, app, client, jwt_svc):
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={'email': 'invitee@example.com', 'role': 'not-a-role'},
            )
        assert response.status_code == 422

    def test_mints_org_scoped_link_with_admin_role(self, app, client, jwt_svc):
        org_id = uuid.uuid4()
        with (
            _available(),
            _authenticated_as(app, 'admin-1'),
            _superadmin(),
            _patch_jwt_service(jwt_svc),
            patch(
                'server.routes.idp.OrgStore.get_org_by_id',
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            response = client.post(
                '/api/idp/signup-links',
                json={
                    'email': 'invitee@example.com',
                    'org_id': str(org_id),
                    'role': 'admin',
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body['role'] == 'admin'
        query = parse_qs(urlparse(body['url']).query)
        token = query['token'][0]
        with _patch_jwt_service(jwt_svc):
            link = idp._verify_signup_link_token(token)
        assert link.role == 'admin'
        assert link.org_id == org_id


# ── POST /oauth/idp/invite — role-driven acceptance (OHE-3510 follow-up) ──


class TestInviteAcceptRole:
    def test_superadmin_role_grants_super_admin_instead_of_org_add(
        self, client, jwt_svc
    ):
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token(
                'invitee@example.com', role='superadmin'
            )
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                ) as mock_grant,
                patch(
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                ) as mock_get_member,
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
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

        mock_grant.assert_awaited_once_with(str(existing.id))
        mock_get_member.assert_not_awaited()
        mock_add.assert_not_awaited()

    def test_org_role_is_passed_through_to_ensure_org_membership(self, client, jwt_svc):
        org_id = uuid.uuid4()
        user_id = derive_idp_user_id('invitee@example.com')
        existing = _mock_user(
            user_id=user_id, email='invitee@example.com', password_hash=None
        )
        mock_role = MagicMock(id=42)
        mock_settings = MagicMock()
        mock_settings.agent_settings.llm.api_key.get_secret_value.return_value = (
            'sk-test'
        )

        with _patch_jwt_service(jwt_svc):
            token = idp._create_signup_link_token(
                'invitee@example.com', org_id=org_id, role='owner'
            )
            with (
                patch(
                    'server.routes.idp._get_integrated_idp_provider',
                    new=AsyncMock(return_value=_fake_integrated_idp_provider()),
                ),
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
                ) as mock_grant,
                patch(
                    'server.routes.idp.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    'server.routes.idp.OrgStore.get_org_by_id',
                    new_callable=AsyncMock,
                    return_value=MagicMock(),
                ),
                patch(
                    'server.routes.idp.RoleStore.get_role_by_name',
                    new_callable=AsyncMock,
                    return_value=mock_role,
                ) as mock_get_role,
                patch(
                    'server.routes.idp.OrgService.create_litellm_integration',
                    new_callable=AsyncMock,
                    return_value=mock_settings,
                ),
                patch(
                    'server.routes.idp.OrgMemberStore.add_user_to_org',
                    new_callable=AsyncMock,
                ) as mock_add,
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

        mock_grant.assert_not_awaited()
        mock_get_role.assert_awaited_once_with('owner')
        mock_add.assert_awaited_once()
        assert mock_add.call_args.kwargs['role_id'] == mock_role.id
