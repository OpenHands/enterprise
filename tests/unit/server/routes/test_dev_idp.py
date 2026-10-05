"""Tests for the dev IDP module — an in-memory sentinel IDP that plugs into
the OAuth v2 flow as an email+password login (OHE-3381).

These tests exercise:

* ``derive_dev_idp_user_id`` — deterministic, case-insensitive UUID derivation
* ``is_dev_idp_available`` — gating logic (self-hosted + no real IDP)
* ``get_dev_idp_if_available`` — sentinel returned when available
* ``GET /oauth/idp-login`` — redirects to the dev IDP sentinel provider
* ``GET /oauth/{DEV_IDP_PROVIDER_ID}/login`` — redirects to the dedicated
  email+password pages instead of starting an OAuth flow
* ``GET /oauth/dev-idp/login`` / ``GET /oauth/dev-idp/signup`` — serve the
  HTML forms
* ``POST /oauth/dev-idp/signup`` — creates an account, hashes the password,
  completes the login
* ``POST /oauth/dev-idp/login`` — verifies the password, completes the login
* Error cases — 404 when unavailable, invalid credentials, taken email,
  password validation
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from server.auth.password_hashing import hash_password
from server.routes import dev_idp
from server.routes.dev_idp import (
    DEV_IDP_LOGIN_PATH,
    DEV_IDP_PROVIDER_ID,
    DEV_IDP_SIGNUP_PATH,
    DevIdpProvider,
    derive_dev_idp_user_id,
    get_dev_idp_if_available,
    is_dev_idp_available,
)

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """FastAPI app with dev_idp_router and oauth_v2_router."""
    from server.routes import oauth_v2

    application = FastAPI()
    # Register dev_idp first (as in saas_server.py) so its literal routes
    # match before the parameterized oauth_v2 routes.
    application.include_router(dev_idp.dev_idp_router)
    application.include_router(dev_idp.dev_idp_status_router)
    application.include_router(oauth_v2.oauth_v2_router)
    return application


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
def _available():
    """Make the dev IDP available (self-hosted, no real IDP configured)."""
    with ExitStack() as stack:
        stack.enter_context(
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted')
        )
        stack.enter_context(
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=False,
            )
        )
        yield


def _patch_complete_login():
    return patch(
        'server.routes.dev_idp._complete_dev_idp_login',
        new_callable=AsyncMock,
    )


# ── derive_dev_idp_user_id ────────────────────────────────────────────────


class TestDeriveDevIdpUserId:
    def test_deterministic(self):
        """Same email always produces the same user id."""
        uid1 = derive_dev_idp_user_id('dev@example.com')
        uid2 = derive_dev_idp_user_id('dev@example.com')
        assert uid1 == uid2

    def test_case_insensitive(self):
        """Email case does not change the derived id."""
        uid1 = derive_dev_idp_user_id('Dev@Example.COM')
        uid2 = derive_dev_idp_user_id('dev@example.com')
        assert uid1 == uid2

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace does not change the derived id."""
        uid1 = derive_dev_idp_user_id('  dev@example.com  ')
        uid2 = derive_dev_idp_user_id('dev@example.com')
        assert uid1 == uid2

    def test_different_emails_different_ids(self):
        """Different emails produce different ids."""
        uid1 = derive_dev_idp_user_id('alice@example.com')
        uid2 = derive_dev_idp_user_id('bob@example.com')
        assert uid1 != uid2

    def test_returns_valid_uuid_string(self):
        """The derived id is a valid UUID string."""
        import uuid

        uid = derive_dev_idp_user_id('dev@example.com')
        parsed = uuid.UUID(uid)
        assert parsed.version == 5


# ── is_dev_idp_available ──────────────────────────────────────────────────


class TestIsDevIdpAvailable:
    def test_cloud_disabled(self):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            import asyncio

            result = asyncio.run(is_dev_idp_available())
        assert result is False

    def test_self_hosted_no_idp(self):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            import asyncio

            result = asyncio.run(is_dev_idp_available())
        assert result is True

    def test_self_hosted_with_idp(self):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            import asyncio

            result = asyncio.run(is_dev_idp_available())
        assert result is False


# ── get_dev_idp_if_available (sentinel) ────────────────────────────────────


class TestGetDevIdpIfAvailable:
    def test_returns_sentinel_when_available(self):
        with _available():
            import asyncio

            result = asyncio.run(get_dev_idp_if_available())
        assert result is not None
        assert isinstance(result, DevIdpProvider)
        assert result.id == DEV_IDP_PROVIDER_ID

    def test_returns_none_on_cloud(self):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            import asyncio

            result = asyncio.run(get_dev_idp_if_available())
        assert result is None

    def test_returns_none_when_real_idp_configured(self):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore._has_real_idp',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            import asyncio

            result = asyncio.run(get_dev_idp_if_available())
        assert result is None


# ── GET /oauth/idp-login redirect ─────────────────────────────────────────


class TestIdpLoginRedirect:
    def test_redirects_to_dev_idp_sentinel(self, client):
        """``/oauth/idp-login`` redirects to ``/oauth/{DEV_IDP_PROVIDER_ID}/login``
        when the dev IDP sentinel is the only IDP available."""
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
            new_callable=AsyncMock,
            return_value=DevIdpProvider(),
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 302
        assert f'/oauth/{DEV_IDP_PROVIDER_ID}/login' in response.headers['location']

    def test_returns_404_when_no_idp_configured(self, client):
        with patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 404


# ── GET /oauth/{DEV_IDP_PROVIDER_ID}/login redirects to the dedicated pages ──


class TestOAuthV2LoginRedirectsDevIdp:
    def test_redirects_to_dev_idp_login_page(self, client):
        with _available():
            response = client.get(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/login', follow_redirects=False
            )
        assert response.status_code == 302
        assert f'/oauth/{DEV_IDP_LOGIN_PATH}' in response.headers['location']

    def test_forwards_redirect_url(self, client):
        with _available():
            response = client.get(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/login',
                params={'redirect_url': '/dashboard'},
                follow_redirects=False,
            )
        location = response.headers['location']
        assert 'redirect_url=%2Fdashboard' in location

    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.get(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/login', follow_redirects=False
            )
        assert response.status_code == 404


# ── GET /oauth/{DEV_IDP_PROVIDER_ID}/callback 404s ────────────────────────


class TestOAuthV2CallbackRejectsDevIdp:
    def test_404_for_dev_idp_provider(self, client):
        response = client.get(
            f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
            params={'code': 'x', 'state': 'y'},
            follow_redirects=False,
        )
        assert response.status_code == 404


# ── GET /oauth/dev-idp/login (HTML form) ──────────────────────────────────


class TestDevIdpLoginForm:
    def test_serves_html_form(self, client):
        with _available():
            response = client.get(f'/oauth/{DEV_IDP_LOGIN_PATH}')
        assert response.status_code == 200
        assert 'text/html' in response.headers.get('content-type', '')
        assert 'email' in response.text.lower()
        assert 'password' in response.text.lower()
        assert 'Development Login' in response.text

    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.get(f'/oauth/{DEV_IDP_LOGIN_PATH}')
        assert response.status_code == 404

    def test_posts_to_login_path(self, client):
        with _available():
            response = client.get(f'/oauth/{DEV_IDP_LOGIN_PATH}')
        assert f'/oauth/{DEV_IDP_LOGIN_PATH}' in response.text

    def test_links_to_signup(self, client):
        with _available():
            response = client.get(f'/oauth/{DEV_IDP_LOGIN_PATH}')
        assert f'/oauth/{DEV_IDP_SIGNUP_PATH}' in response.text

    def test_shows_error_message(self, client):
        with _available():
            response = client.get(
                f'/oauth/{DEV_IDP_LOGIN_PATH}', params={'error': 'invalid_credentials'}
            )
        assert 'Invalid email or password' in response.text


# ── GET /oauth/dev-idp/signup (HTML form) ─────────────────────────────────


class TestDevIdpSignupForm:
    def test_serves_html_form(self, client):
        with _available():
            response = client.get(f'/oauth/{DEV_IDP_SIGNUP_PATH}')
        assert response.status_code == 200
        assert 'confirm_password' in response.text
        assert 'Development Sign Up' in response.text

    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.get(f'/oauth/{DEV_IDP_SIGNUP_PATH}')
        assert response.status_code == 404

    def test_links_to_login(self, client):
        with _available():
            response = client.get(f'/oauth/{DEV_IDP_SIGNUP_PATH}')
        assert f'/oauth/{DEV_IDP_LOGIN_PATH}' in response.text


# ── POST /oauth/dev-idp/signup ────────────────────────────────────────────


class TestDevIdpSignup:
    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.post(
                f'/oauth/{DEV_IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 404

    def test_password_mismatch_redirects_with_error(self, client):
        with _available():
            response = client.post(
                f'/oauth/{DEV_IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'different123',
                },
                follow_redirects=False,
            )
        assert response.status_code == 302
        location = response.headers['location']
        assert DEV_IDP_SIGNUP_PATH in location
        query = parse_qs(urlparse(location).query)
        assert query['error'] == ['password_mismatch']

    def test_password_too_short_redirects_with_error(self, client):
        with _available():
            response = client.post(
                f'/oauth/{DEV_IDP_SIGNUP_PATH}',
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
        existing = _mock_user(email='dev@example.com')
        with (
            _available(),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=existing,
            ),
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_SIGNUP_PATH}',
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

    def test_creates_user_and_hashes_password(self, client):
        user_id = derive_dev_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)

        with (
            _available(),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.dev_idp.UserStore.create_user',
                new_callable=AsyncMock,
                return_value=mock_user,
            ) as mock_create,
            patch(
                'server.routes.dev_idp._set_password_hash',
                new_callable=AsyncMock,
            ) as mock_set_hash,
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            client.post(
                f'/oauth/{DEV_IDP_SIGNUP_PATH}',
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
            _available(),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.dev_idp.UserStore.create_user',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_SIGNUP_PATH}',
                data={
                    'email': 'dev@example.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                },
            )
        assert response.status_code == 500


# ── POST /oauth/dev-idp/login ─────────────────────────────────────────────


class TestDevIdpLogin:
    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.post(
                f'/oauth/{DEV_IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'password123'},
            )
        assert response.status_code == 404

    def test_unknown_email_redirects_with_error(self, client):
        with (
            _available(),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_LOGIN_PATH}',
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
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_LOGIN_PATH}',
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
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_LOGIN_PATH}',
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
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            response = client.post(
                f'/oauth/{DEV_IDP_LOGIN_PATH}',
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
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            _patch_complete_login() as mock_complete,
        ):
            mock_complete.return_value = RedirectResponse('/', status_code=302)
            client.post(
                f'/oauth/{DEV_IDP_LOGIN_PATH}',
                data={'email': 'dev@example.com', 'password': 'correct-password'},
                follow_redirects=False,
            )
        mock_complete.assert_awaited_once()


# ── _complete_dev_idp_login — shared post-auth steps ──────────────────────


class TestCompleteDevIdpLogin:
    @pytest.mark.asyncio
    async def test_tos_auto_accepted_for_new_user(self):
        user_id = derive_dev_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)
        request = MagicMock()

        with (
            patch(
                'server.routes.dev_idp._accept_tos_for_dev_user',
                new_callable=AsyncMock,
            ) as mock_accept_tos,
            patch(
                'server.routes.dev_idp.UserStore.record_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.dev_idp.DefaultOrgBootstrapService.apply_for_user',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                'server.routes.dev_idp._track_dev_idp_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.dev_idp._should_redirect_to_onboarding_dev',
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch('server.routes.dev_idp._set_dev_idp_cookie'),
            patch(
                'server.routes.dev_idp.get_web_url',
                return_value='http://testserver',
            ),
        ):
            response = await dev_idp._complete_dev_idp_login(
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

        user_id = derive_dev_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=datetime.now(timezone.utc))
        request = MagicMock()

        with (
            patch(
                'server.routes.dev_idp._accept_tos_for_dev_user',
                new_callable=AsyncMock,
            ) as mock_accept_tos,
            patch(
                'server.routes.dev_idp.UserStore.record_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.dev_idp._track_dev_idp_login',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.dev_idp._should_redirect_to_onboarding_dev',
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch('server.routes.dev_idp._set_dev_idp_cookie'),
            patch(
                'server.routes.dev_idp.get_web_url',
                return_value='http://testserver',
            ),
        ):
            await dev_idp._complete_dev_idp_login(
                request=request,
                user=mock_user,
                is_new_user=False,
                email='dev@example.com',
                redirect_url='',
            )
        mock_accept_tos.assert_not_called()


# ── status endpoint ───────────────────────────────────────────────────────


class TestDevIdpStatus:
    def test_returns_enabled_true(self, client):
        with _available():
            response = client.get('/api/dev-idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is True

    def test_returns_enabled_false_on_cloud(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.get('/api/dev-idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is False
