"""Route tests for the development-only insecure IDP (OHE-3381).

Exercises:
- ``GET /api/dev-idp/status`` — enabled/disabled based on deployment mode + IDP config
- ``POST /api/dev-idp/login`` — email-only login flow: user creation, cookie set,
  redirect URL, TOS auto-accept, onboarding redirect
- Availability gating: disabled on cloud, disabled when a real IDP exists
- Deterministic user-id derivation from email
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.routes import dev_idp


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
    application = FastAPI()
    application.include_router(dev_idp.dev_idp_router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _fake_user(
    *,
    user_id: str | UUID | None = None,
    email: str = 'dev@example.com',
    accepted_tos: datetime | None = None,
    onboarding_completed: bool | None = None,
):
    user = MagicMock()
    if user_id is not None:
        user.id = UUID(str(user_id)) if isinstance(user_id, str) else user_id
    else:
        user.id = UUID('12345678-1234-5678-1234-567812345678')
    user.email = email
    user.accepted_tos = accepted_tos
    user.onboarding_completed = onboarding_completed
    user.current_org_id = UUID('12345678-1234-5678-1234-567812345678')
    user.user_consents_to_analytics = False
    return user


# ── user-id derivation ──────────────────────────────────────────────────


def test_derive_user_id_deterministic():
    """Same email → same user id, regardless of case/whitespace."""
    id1 = dev_idp.derive_dev_idp_user_id('Alice@Example.COM')
    id2 = dev_idp.derive_dev_idp_user_id('  alice@example.com  ')
    assert id1 == id2
    # Different email → different id
    id3 = dev_idp.derive_dev_idp_user_id('bob@example.com')
    assert id1 != id3


def test_derive_user_id_is_uuid():
    """The derived id is a valid UUID string."""
    uid = dev_idp.derive_dev_idp_user_id('test@example.com')
    parsed = UUID(uid)
    assert parsed.version == 5  # uuid5 (SHA-1 based)


# ── status endpoint ─────────────────────────────────────────────────────


def test_status_disabled_on_cloud(client, jwt_svc):
    with (
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'cloud'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.get('/api/dev-idp/status')
    assert response.status_code == 200
    assert response.json() == {'enabled': False}


def test_status_enabled_self_hosted_no_idp(client, jwt_svc):
    with (
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.get('/api/dev-idp/status')
    assert response.status_code == 200
    assert response.json() == {'enabled': True}


def test_status_disabled_when_real_idp_configured(client, jwt_svc):
    fake_provider = MagicMock()
    fake_provider.is_idp = True
    with (
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[fake_provider]),
        ),
    ):
        response = client.get('/api/dev-idp/status')
    assert response.status_code == 200
    assert response.json() == {'enabled': False}


# ── login endpoint ──────────────────────────────────────────────────────


def test_login_disabled_on_cloud_returns_404(client, jwt_svc):
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'cloud'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': 'dev@example.com', 'redirect_url': '/'},
        )
    assert response.status_code == 404


def test_login_disabled_when_idp_configured_returns_404(client, jwt_svc):
    fake_provider = MagicMock()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[fake_provider]),
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': 'dev@example.com'},
        )
    assert response.status_code == 404


def test_login_new_user_creates_user_and_sets_cookie(client, jwt_svc):
    email = 'newuser@example.com'
    user_id = dev_idp.derive_dev_idp_user_id(email)
    fake_user = _fake_user(
        user_id=user_id,
        email=email,
        accepted_tos=None,
        onboarding_completed=None,
    )

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            dev_idp.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(dev_idp.UserStore, 'record_login', new=AsyncMock()),
        patch.object(dev_idp, '_accept_tos_for_dev_user', new=AsyncMock()),
        patch.object(
            dev_idp.DefaultOrgBootstrapService,
            'apply_for_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(dev_idp, '_track_dev_idp_login', new=AsyncMock()),
        patch.object(
            dev_idp,
            '_should_redirect_to_onboarding_dev',
            new=AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.auth._build_cross_app_redirect_url',
            return_value='/dashboard',
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email, 'redirect_url': '/dashboard'},
        )

    assert response.status_code == 200
    body = response.json()
    assert 'redirect_url' in body
    # Cookie should be set
    assert 'openhands_auth' in response.cookies


def test_login_existing_user_by_id_reuses_user(client, jwt_svc):
    email = 'existing@example.com'
    user_id = dev_idp.derive_dev_idp_user_id(email)
    fake_user = _fake_user(
        user_id=user_id,
        email=email,
        accepted_tos=datetime.now(timezone.utc),
        onboarding_completed=True,
    )

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            dev_idp.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ) as create_user,
        patch.object(dev_idp.UserStore, 'record_login', new=AsyncMock()),
        patch.object(dev_idp, '_track_dev_idp_login', new=AsyncMock()),
        patch.object(
            dev_idp,
            '_should_redirect_to_onboarding_dev',
            new=AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.auth._build_cross_app_redirect_url',
            return_value='/',
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email},
        )

    assert response.status_code == 200
    # create_user should NOT have been called (user already exists)
    create_user.assert_not_awaited()


def test_login_existing_user_by_email_fallback(client, jwt_svc):
    """When the derived user_id doesn't match but the email does, reuse
    the existing user (e.g. a user created by a real IDP before the switch)."""
    email = 'realidpuser@example.com'
    # User exists with a different id (from a real IDP's sub)
    real_idp_user_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    fake_user = _fake_user(
        user_id=real_idp_user_id,
        email=email,
        accepted_tos=datetime.now(timezone.utc),
        onboarding_completed=True,
    )

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        # get_user_by_id returns None (derived id doesn't match)
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=None),
        ),
        # get_user_by_email finds the existing user
        patch.object(
            dev_idp.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(
            dev_idp.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ) as create_user,
        patch.object(dev_idp.UserStore, 'record_login', new=AsyncMock()),
        patch.object(dev_idp, '_track_dev_idp_login', new=AsyncMock()),
        patch.object(
            dev_idp,
            '_should_redirect_to_onboarding_dev',
            new=AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.auth._build_cross_app_redirect_url',
            return_value='/',
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email},
        )

    assert response.status_code == 200
    # create_user should NOT have been called
    create_user.assert_not_awaited()


def test_login_invalid_email_returns_422(client, jwt_svc):
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': 'not-an-email'},
        )
    assert response.status_code == 422


def test_login_auto_accepts_tos_for_new_user(client, jwt_svc):
    email = 'notosuser@example.com'
    user_id = dev_idp.derive_dev_idp_user_id(email)
    fake_user = _fake_user(
        user_id=user_id,
        email=email,
        accepted_tos=None,
        onboarding_completed=None,
    )

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            dev_idp.UserStore,
            'create_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(dev_idp.UserStore, 'record_login', new=AsyncMock()),
        patch.object(
            dev_idp, '_accept_tos_for_dev_user', new=AsyncMock()
        ) as accept_tos,
        patch.object(
            dev_idp.DefaultOrgBootstrapService,
            'apply_for_user',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(dev_idp, '_track_dev_idp_login', new=AsyncMock()),
        patch.object(
            dev_idp,
            '_should_redirect_to_onboarding_dev',
            new=AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.auth._build_cross_app_redirect_url',
            return_value='/',
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email},
        )

    assert response.status_code == 200
    # TOS acceptance should have been called since accepted_tos was None
    accept_tos.assert_awaited_once_with(user_id)


def test_login_does_not_accept_tos_if_already_accepted(client, jwt_svc):
    email = 'tosaccepted@example.com'
    user_id = dev_idp.derive_dev_idp_user_id(email)
    fake_user = _fake_user(
        user_id=user_id,
        email=email,
        accepted_tos=datetime.now(timezone.utc),
        onboarding_completed=True,
    )

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(dev_idp.UserStore, 'record_login', new=AsyncMock()),
        patch.object(
            dev_idp, '_accept_tos_for_dev_user', new=AsyncMock()
        ) as accept_tos,
        patch.object(dev_idp, '_track_dev_idp_login', new=AsyncMock()),
        patch.object(
            dev_idp,
            '_should_redirect_to_onboarding_dev',
            new=AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.auth._build_cross_app_redirect_url',
            return_value='/',
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email},
        )

    assert response.status_code == 200
    # TOS already accepted — should not call _accept_tos_for_dev_user
    accept_tos.assert_not_awaited()


def test_login_redirects_to_onboarding_when_required(client, jwt_svc):
    email = 'onboarding@example.com'
    user_id = dev_idp.derive_dev_idp_user_id(email)
    fake_user = _fake_user(
        user_id=user_id,
        email=email,
        accepted_tos=datetime.now(timezone.utc),
        onboarding_completed=False,
    )

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=fake_user),
        ),
        patch.object(dev_idp.UserStore, 'record_login', new=AsyncMock()),
        patch.object(dev_idp, '_track_dev_idp_login', new=AsyncMock()),
        patch.object(
            dev_idp,
            '_should_redirect_to_onboarding_dev',
            new=AsyncMock(return_value=True),
        ),
        patch(
            'server.routes.auth._build_onboarding_redirect',
            return_value='/onboarding?returnTo=%2Fdashboard',
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email, 'redirect_url': '/dashboard'},
        )

    assert response.status_code == 200
    assert '/onboarding' in response.json()['redirect_url']


def test_login_create_user_failure_returns_500(client, jwt_svc):
    email = 'failuser@example.com'

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(dev_idp, 'DEPLOYMENT_MODE', 'self_hosted'),
        patch.object(
            dev_idp.OAuthProviderStore,
            'get_idp_providers',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_id',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            dev_idp.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            dev_idp.UserStore,
            'create_user',
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.post(
            '/api/dev-idp/login',
            json={'email': email},
        )

    assert response.status_code == 500
