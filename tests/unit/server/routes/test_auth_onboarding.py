"""Tests for post-auth redirect routes and functions.

Tests for:
- /accept_tos endpoint
- _get_post_auth_redirect() function
- _build_cross_app_redirect_url() function
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from sqlalchemy import select

from server.auth.saas_user_auth import SaasUserAuth
from server.routes.auth import (
    _build_cross_app_redirect_url,
    _get_post_auth_redirect,
    accept_tos,
)
from storage.instance_settings import InstanceSettings
from storage.role import Role
from storage.user import User

# --- Fixtures ---


@pytest.fixture
def mock_request():
    """Create a mock FastAPI Request."""
    request = MagicMock(spec=Request)
    request.url = MagicMock()
    request.url.hostname = 'localhost'
    request.url.netloc = 'localhost:8000'
    request.base_url = 'http://localhost:8000/'
    request.headers = {}
    request.cookies = {}
    return request


@pytest.fixture
def mock_user():
    """Create a mock User object."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.current_org_id = uuid.uuid4()
    return user


@pytest.mark.asyncio
@pytest.mark.parametrize('email', ['signup@example.com', None])
async def test_accept_tos_sets_signup_email_and_timestamp_together(
    email, mock_request, create_user, async_session_maker
):
    user = create_user(email=email)
    mock_request.json = AsyncMock(return_value={'redirect_url': 'https://example.com/'})
    user_auth = MagicMock(spec=SaasUserAuth)
    user_auth.oauth_v2_cookie = True
    user_auth.get_user_id = AsyncMock(return_value=str(user.id))
    analytics = MagicMock()

    with (
        patch('server.routes.auth.get_user_auth', AsyncMock(return_value=user_auth)),
        patch('server.routes.auth.a_session_maker', async_session_maker),
        patch('server.routes.auth.get_web_url', return_value='https://example.com'),
        patch('server.routes.auth.get_analytics_service', return_value=analytics),
        patch(
            'server.routes.auth._get_post_auth_redirect',
            AsyncMock(return_value='https://example.com/'),
        ),
        patch('server.auth.provider_identity.set_oauth_v2_cookie'),
    ):
        response = await accept_tos(mock_request)

    assert response.status_code == 200
    analytics.track_user_signed_up.assert_called_once()
    analytics.set_person_properties.assert_called_once()
    properties = analytics.set_person_properties.call_args.kwargs['properties']
    assert properties.keys() == (
        {'email', 'signed_up_at'} if email else {'signed_up_at'}
    )
    if email:
        assert properties['email'] == email
    assert (
        datetime.fromisoformat(properties['signed_up_at']).date()
        == datetime.now(timezone.utc).date()
    )


def _super_admin_role_id(session_maker) -> int:
    """The ``admin`` role row, which doubles as the instance Super Admin role."""
    with session_maker() as session:
        role = session.scalar(select(Role).where(Role.name == 'admin'))
        if role is None:
            role = Role(name='admin', rank=1)
            session.add(role)
            session.commit()
        return role.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'first_super_admin', [True, False], ids=['first-super-admin', 'regular-user']
)
async def test_accept_tos_marks_the_first_super_admin_on_the_signup_event(
    first_super_admin, mock_request, create_user, session_maker, async_session_maker
):
    # Arrange
    user = create_user(
        email='admin@example.com',
        role_id=_super_admin_role_id(session_maker) if first_super_admin else None,
    )
    if first_super_admin:
        await _record_first_super_admin(async_session_maker, str(user.id))
    mock_request.json = AsyncMock(return_value={'redirect_url': 'https://example.com/'})
    user_auth = MagicMock(spec=SaasUserAuth)
    user_auth.oauth_v2_cookie = True
    user_auth.get_user_id = AsyncMock(return_value=str(user.id))
    analytics = MagicMock()

    # Act
    with (
        patch('server.routes.auth.get_user_auth', AsyncMock(return_value=user_auth)),
        patch('server.routes.auth.a_session_maker', async_session_maker),
        patch('server.routes.auth.ENABLE_SUPER_ADMIN', True),
        patch('server.routes.auth.get_web_url', return_value='https://example.com'),
        patch('server.routes.auth.get_analytics_service', return_value=analytics),
        patch(
            'server.routes.auth._get_post_auth_redirect',
            AsyncMock(return_value='https://example.com/'),
        ),
        patch('server.auth.provider_identity.set_oauth_v2_cookie'),
    ):
        response = await accept_tos(mock_request)

    # Assert
    assert response.status_code == 200
    kwargs = analytics.track_user_signed_up.call_args.kwargs
    assert kwargs['is_super_admin'] is first_super_admin
    assert kwargs['is_instance_setup_user'] is first_super_admin


# --- Tests for _get_post_auth_redirect ---


class TestGetPostAuthRedirect:
    """Tests for the _get_post_auth_redirect function."""

    @pytest.mark.asyncio
    async def test_returns_default_url(self, mock_user):
        """Test that default URL is returned when no special flow is pending."""
        user_id = str(mock_user.id)

        result = await _get_post_auth_redirect(
            user_id, 'https://example.com/dashboard', 'https://example.com'
        )

        assert result == 'https://example.com/dashboard'

    @pytest.mark.asyncio
    async def test_unwraps_automation_login_redirect(self, mock_user):
        """Automation redirects should bypass the main app login SPA."""
        user_id = str(mock_user.id)

        result = await _get_post_auth_redirect(
            user_id,
            'https://example.com/login?redirect=%2Fautomations&login_method=github',
            'https://example.com',
        )

        assert result == 'https://example.com/automations?login_method=github'


# --- Tests for routing the first Super Admin into the install wizard ---


@pytest.fixture
def setup_state_db(async_session_maker):
    """Read ``instance_settings`` from the test database, with the flag on."""
    with (
        patch('server.routes.auth.a_session_maker', async_session_maker),
        patch('server.routes.auth.ENABLE_SUPER_ADMIN', True),
    ):
        yield async_session_maker


async def _record_first_super_admin(
    async_session_maker, user_id: str, wizard_completed: bool = False
) -> None:
    async with async_session_maker() as session:
        session.add(
            InstanceSettings(
                id=1,
                setup_user_id=uuid.UUID(user_id),
                wizard_completed=wizard_completed,
            )
        )
        await session.commit()


class TestFirstSuperAdminInstallRedirect:
    """The first Super Admin goes to /install until the wizard is finished."""

    @pytest.mark.asyncio
    async def test_sends_first_super_admin_to_install_while_wizard_pending(
        self, mock_user, setup_state_db
    ):
        # Arrange
        user_id = str(mock_user.id)
        await _record_first_super_admin(setup_state_db, user_id)

        # Act
        result = await _get_post_auth_redirect(
            user_id, 'https://example.com/canvas', 'https://example.com'
        )

        # Assert
        assert result == 'https://example.com/install'

    @pytest.mark.asyncio
    async def test_sends_first_super_admin_to_default_url_once_wizard_finished(
        self, mock_user, setup_state_db
    ):
        # Arrange
        user_id = str(mock_user.id)
        await _record_first_super_admin(setup_state_db, user_id, wizard_completed=True)

        # Act
        result = await _get_post_auth_redirect(
            user_id, 'https://example.com/canvas', 'https://example.com'
        )

        # Assert
        assert result == 'https://example.com/canvas'

    @pytest.mark.asyncio
    async def test_sends_other_users_to_default_url(self, mock_user, setup_state_db):
        # Arrange
        await _record_first_super_admin(setup_state_db, str(uuid.uuid4()))

        # Act
        result = await _get_post_auth_redirect(
            str(mock_user.id), 'https://example.com/', 'https://example.com'
        )

        # Assert
        assert result == 'https://example.com/'

    @pytest.mark.asyncio
    async def test_sends_first_super_admin_to_default_url_when_flag_is_off(
        self, mock_user, setup_state_db
    ):
        # Arrange
        user_id = str(mock_user.id)
        await _record_first_super_admin(setup_state_db, user_id)

        # Act
        with patch('server.routes.auth.ENABLE_SUPER_ADMIN', False):
            result = await _get_post_auth_redirect(
                user_id, 'https://example.com/', 'https://example.com'
            )

        # Assert
        assert result == 'https://example.com/'


class TestBuildCrossAppRedirectUrl:
    def test_unwraps_legacy_login_redirect_to_automations(self):
        result = _build_cross_app_redirect_url(
            'https://example.com/login?redirect=%2Fautomations&login_method=github',
            'https://example.com',
        )

        assert result == 'https://example.com/automations?login_method=github'

    def test_unwraps_login_return_to_automation_deep_link(self):
        result = _build_cross_app_redirect_url(
            'https://example.com/login'
            '?returnTo=%2Fautomations%2Fabc%3Ftab%3Druns'
            '&login_method=gitlab',
            'https://example.com',
        )

        assert result == (
            'https://example.com/automations/abc?tab=runs&login_method=gitlab'
        )

    def test_direct_automation_path_becomes_same_origin_absolute_url(self):
        result = _build_cross_app_redirect_url('/automations', 'https://example.com')

        assert result == 'https://example.com/automations'

    def test_unwraps_login_return_to_canvas_deep_link(self):
        result = _build_cross_app_redirect_url(
            'https://example.com/login'
            '?returnTo=%2Fcanvas%2Fconversations%2Fabc'
            '&login_method=github',
            'https://example.com',
        )

        assert result == (
            'https://example.com/canvas/conversations/abc?login_method=github'
        )

    def test_direct_canvas_path_becomes_same_origin_absolute_url(self):
        result = _build_cross_app_redirect_url('/canvas', 'https://example.com')

        assert result == 'https://example.com/canvas'

    def test_non_automation_login_redirect_is_unchanged(self):
        result = _build_cross_app_redirect_url(
            'https://example.com/login?redirect=%2Fsettings&login_method=github',
            'https://example.com',
        )

        assert result == (
            'https://example.com/login?redirect=%2Fsettings&login_method=github'
        )

    def test_rejects_protocol_relative_inner_redirect(self):
        result = _build_cross_app_redirect_url(
            'https://example.com/login?redirect=%2F%2Fevil.example.com%2Fautomations',
            'https://example.com',
        )

        assert result == (
            'https://example.com/login?redirect=%2F%2Fevil.example.com%2Fautomations'
        )
