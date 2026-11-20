"""Unit tests for GET /api/v1/release (release_router.py).

Calls ``get_release_info`` directly with a mocked ``UserContext`` (same
pattern as ``tests/unit/app_server/test_user_git_info.py``) rather than a
``TestClient``/app lifespan: no database or Docker is required.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openhands.app_server.status import system_stats
from openhands.app_server.status.release_router import get_release_info
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.version import get_version


@pytest.fixture
def mock_user_context():
    """A mocked UserContext resolving to an authenticated user by default."""
    context = AsyncMock()
    context.get_user_info = AsyncMock(return_value=UserInfo(id='user-123'))
    return context


class TestGetReleaseInfo:
    async def test_raises_401_when_unauthenticated(self, mock_user_context):
        """No resolvable user -> 401, matching /users/me's behavior."""
        mock_user_context.get_user_info = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_release_info(user_context=mock_user_context)

        assert exc_info.value.status_code == 401

    async def test_returns_app_and_sdk_version(self, mock_user_context):
        """app_version/sdk_version always come from the shared providers."""
        result = await get_release_info(user_context=mock_user_context)

        assert result.app_version == get_version()
        assert result.sdk_version == system_stats.get_sdk_version()

    async def test_enterprise_release_absent_by_default(
        self, monkeypatch, mock_user_context
    ):
        """Deterministic absent case: explicitly unset regardless of the
        ambient environment, rather than relying on it already being unset."""
        monkeypatch.delenv('OPENHANDS_ENTERPRISE_RELEASE', raising=False)

        result = await get_release_info(user_context=mock_user_context)

        assert result.enterprise_release is None

    async def test_enterprise_release_empty_string_is_treated_as_absent(
        self, monkeypatch, mock_user_context
    ):
        """An explicitly empty value (e.g. an unset Helm value rendered as
        an empty string) must not be surfaced as a non-null release."""
        monkeypatch.setenv('OPENHANDS_ENTERPRISE_RELEASE', '')

        result = await get_release_info(user_context=mock_user_context)

        assert result.enterprise_release is None

    async def test_enterprise_release_from_env(self, monkeypatch, mock_user_context):
        """When packaging sets OPENHANDS_ENTERPRISE_RELEASE, it is surfaced
        as supplied, independently of app_version."""
        monkeypatch.setenv('OPENHANDS_ENTERPRISE_RELEASE', '0.110.0')

        result = await get_release_info(user_context=mock_user_context)

        assert result.enterprise_release == '0.110.0'
        assert result.app_version == get_version()

    async def test_does_not_include_resource_or_uptime_fields(self, mock_user_context):
        """This endpoint must never grow the diagnostics-only fields
        (uptime, idle_time, resources) that /server_info returns."""
        result = await get_release_info(user_context=mock_user_context)

        dumped = result.model_dump()
        assert set(dumped) == {'app_version', 'sdk_version', 'enterprise_release'}
