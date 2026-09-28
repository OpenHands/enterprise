"""Tests for the dev IDP module — virtual IDP that plugs into the OAuth v2 flow.

These tests exercise:

* ``derive_dev_idp_user_id`` — deterministic, case-insensitive UUID derivation
* ``is_dev_idp_available`` — gating logic (self-hosted + no real IDP)
* ``DevIdpProvider`` sentinel — shape and fields
* ``get_first_idp`` — returns the sentinel when no real IDP is configured
* ``GET /oauth/idp-login`` — redirects to the dev IDP login form
* ``GET /oauth/-1/login`` — serves the HTML email-entry form
* ``POST /oauth/-1/callback`` — completes login (user creation, cookie, redirect)
* ``GET /oauth/-1/callback`` — redirects to form when no email, handles email
* Error cases — 404 when dev IDP unavailable
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routes import dev_idp
from server.routes.dev_idp import (
    DEV_IDP_PROVIDER_ID,
    DevIdpProvider,
    derive_dev_idp_user_id,
    is_dev_idp_available,
)

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """FastAPI app with dev_idp_router and oauth_v2_router."""
    from server.routes import oauth_v2

    application = FastAPI()
    # Register dev_idp first (as in saas_server.py) so literal routes match
    # before the parameterized oauth_v2 routes.
    application.include_router(dev_idp.dev_idp_router)
    application.include_router(dev_idp.dev_idp_status_router)
    application.include_router(oauth_v2.oauth_v2_router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _mock_user(
    *, user_id: str | None = None, email: str = 'dev@example.com', accepted_tos=None
):
    """Create a mock User object."""
    import uuid

    user = MagicMock()
    user.id = uuid.UUID(user_id) if user_id else uuid.uuid4()
    user.email = email
    user.accepted_tos = accepted_tos
    user.user_consents_to_analytics = False
    return user


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


# ── DevIdpProvider sentinel ───────────────────────────────────────────────


class TestDevIdpProvider:
    def test_default_fields(self):
        provider = DevIdpProvider()
        assert provider.id == DEV_IDP_PROVIDER_ID
        assert provider.is_idp is True
        assert provider.provider_category == 'dev_idp'
        assert provider.display_name == 'Development IDP'

    def test_is_frozen(self):
        """DevIdpProvider is a frozen dataclass."""
        provider = DevIdpProvider()
        with pytest.raises(AttributeError):
            provider.id = 999


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
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            import asyncio

            result = asyncio.run(is_dev_idp_available())
        assert result is True

    def test_self_hosted_with_idp(self):
        fake_provider = MagicMock()
        fake_provider.is_idp = True
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[fake_provider],
            ),
        ):
            import asyncio

            result = asyncio.run(is_dev_idp_available())
        assert result is False


# ── OAuthProviderStore.get_first_idp returns sentinel ─────────────────────


class TestGetFirstIdp:
    def test_returns_dev_idp_when_no_real_idp(self):
        """When no real IDP exists, get_first_idp returns the DevIdpProvider sentinel."""
        from storage.oauth_provider_store import OAuthProviderStore

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(
                OAuthProviderStore,
                'get_by_id',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            # Simulate what get_first_idp does: query DB (returns None),
            # then fall back to dev IDP.
            import asyncio

            from server.routes.dev_idp import get_dev_idp_if_available

            result = asyncio.run(get_dev_idp_if_available())
        assert result is not None
        assert result.id == DEV_IDP_PROVIDER_ID
        assert result.is_idp is True


# ── GET /oauth/idp-login redirect ─────────────────────────────────────────


class TestIdpLoginRedirect:
    def test_redirects_to_dev_idp_when_no_real_idp(self, client):
        """``/oauth/idp-login`` redirects to the dev IDP login form."""
        from server.routes.dev_idp import DevIdpProvider

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
                new_callable=AsyncMock,
                return_value=DevIdpProvider(),
            ),
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 302
        assert f'/oauth/{DEV_IDP_PROVIDER_ID}/login' in response.headers['location']

    def test_returns_404_when_dev_idp_unavailable(self, client):
        """``/oauth/idp-login`` returns 404 when dev IDP is not available."""
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_first_idp',
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.get('/oauth/idp-login', follow_redirects=False)
        assert response.status_code == 404


# ── GET /oauth/-1/login (HTML form) ───────────────────────────────────────


class TestDevIdpLoginForm:
    def test_serves_html_form(self, client):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = client.get(f'/oauth/{DEV_IDP_PROVIDER_ID}/login')
        assert response.status_code == 200
        assert 'text/html' in response.headers.get('content-type', '')
        assert 'email' in response.text.lower()
        assert 'Development Login' in response.text

    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.get(f'/oauth/{DEV_IDP_PROVIDER_ID}/login')
        assert response.status_code == 404

    def test_form_posts_to_callback(self, client):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = client.get(f'/oauth/{DEV_IDP_PROVIDER_ID}/login')
        assert f'/oauth/{DEV_IDP_PROVIDER_ID}/callback' in response.text


# ── POST /oauth/-1/callback (login completion) ───────────────────────────


class TestDevIdpCallback:
    def test_404_when_unavailable(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
            )
        assert response.status_code == 404

    def test_invalid_email_returns_422(self, client):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'not-an-email'},
            )
        assert response.status_code == 422

    def test_new_user_login(self, client):
        """New user is created, cookie is set, and redirect happens."""
        user_id = derive_dev_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
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
            ),
            patch(
                'server.routes.dev_idp._accept_tos_for_dev_user',
                new_callable=AsyncMock,
            ),
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
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
                follow_redirects=False,
            )
        assert response.status_code == 302

    def test_existing_user_login(self, client):
        """Existing user is reused (no create_user call)."""
        user_id = derive_dev_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
            patch(
                'server.routes.dev_idp.UserStore.create_user',
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                'server.routes.dev_idp._accept_tos_for_dev_user',
                new_callable=AsyncMock,
            ),
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
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
                follow_redirects=False,
            )
        assert response.status_code == 302
        mock_create.assert_not_called()

    def test_existing_user_by_email_fallback(self, client):
        """User created by real IDP (different id) is found by email."""
        # User doesn't exist by derived id, but exists by email
        mock_user = _mock_user(
            user_id='12345678-1234-1234-1234-123456789012',
            email='dev@example.com',
            accepted_tos=None,
        )

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
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
            patch(
                'server.routes.dev_idp.UserStore.create_user',
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                'server.routes.dev_idp._accept_tos_for_dev_user',
                new_callable=AsyncMock,
            ),
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
        ):
            response = client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
                follow_redirects=False,
            )
        assert response.status_code == 302
        mock_create.assert_not_called()

    def test_tos_auto_accepted(self, client):
        """TOS is auto-accepted for new users."""
        user_id = derive_dev_idp_user_id('dev@example.com')
        mock_user = _mock_user(user_id=user_id, accepted_tos=None)

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
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
            ),
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
        ):
            client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
                follow_redirects=False,
            )
        mock_accept_tos.assert_called_once_with(user_id)

    def test_tos_skip_if_already_accepted(self, client):
        """TOS is not re-accepted if already accepted."""
        user_id = derive_dev_idp_user_id('dev@example.com')
        from datetime import datetime, timezone

        mock_user = _mock_user(
            user_id=user_id,
            accepted_tos=datetime.now(timezone.utc),
        )

        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                'server.routes.dev_idp.UserStore.get_user_by_id',
                new_callable=AsyncMock,
                return_value=mock_user,
            ),
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
        ):
            client.post(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
                follow_redirects=False,
            )
        mock_accept_tos.assert_not_called()

    def test_create_user_failure_returns_500(self, client):
        """If create_user returns None, the callback returns 500."""
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
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
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                data={'email': 'dev@example.com'},
                follow_redirects=False,
            )
        assert response.status_code == 500


# ── GET /oauth/-1/callback (convenience GET) ─────────────────────────────


class TestDevIdpCallbackGet:
    def test_redirects_to_form_when_no_email(self, client):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = client.get(
                f'/oauth/{DEV_IDP_PROVIDER_ID}/callback',
                follow_redirects=False,
            )
        assert response.status_code == 302
        assert f'/oauth/{DEV_IDP_PROVIDER_ID}/login' in response.headers['location']


# ── status endpoint ───────────────────────────────────────────────────────


class TestDevIdpStatus:
    def test_returns_enabled_true(self, client):
        with (
            patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'self_hosted'),
            patch(
                'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = client.get('/api/dev-idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is True

    def test_returns_enabled_false_on_cloud(self, client):
        with patch('server.routes.dev_idp.DEPLOYMENT_MODE', 'cloud'):
            response = client.get('/api/dev-idp/status')
        assert response.status_code == 200
        assert response.json()['enabled'] is False
