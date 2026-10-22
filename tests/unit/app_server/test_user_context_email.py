"""Unit tests for ``UserContext.get_user_email()`` implementations."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.specifiy_user_context import (
    ADMIN,
    SpecifyUserContext,
)
from openhands.app_server.user_auth.user_auth import UserAuth


class TestAuthUserContextEmail:
    """``AuthUserContext.get_user_email()`` delegates to ``UserAuth``."""

    @pytest.mark.asyncio
    async def test_returns_email_when_user_auth_has_one(self):
        """SaaS mode: Keycloak-backed UserAuth returns the user's email."""
        user_auth = AsyncMock()
        user_auth.get_user_email = AsyncMock(return_value='alice@example.com')
        ctx = AuthUserContext(user_auth=user_auth)

        email = await ctx.get_user_email()

        assert email == 'alice@example.com'
        user_auth.get_user_email.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_user_auth_has_no_email(self):
        """OSS mode: DefaultUserAuth returns None and we propagate that."""
        user_auth = AsyncMock()
        user_auth.get_user_email = AsyncMock(return_value=None)
        ctx = AuthUserContext(user_auth=user_auth)

        email = await ctx.get_user_email()

        assert email is None
        user_auth.get_user_email.assert_awaited_once()


class TestAuthUserContextEffectiveOrg:
    """``AuthUserContext.get_effective_org_id()`` delegates when supported."""

    @pytest.mark.asyncio
    async def test_delegates_to_user_auth_when_available(self):
        org_id = uuid4()
        user_auth = MagicMock(spec=UserAuth)
        user_auth.get_effective_org_id = AsyncMock(return_value=org_id)
        ctx = AuthUserContext(user_auth=user_auth)

        effective_org_id = await ctx.get_effective_org_id()

        assert effective_org_id == org_id
        user_auth.get_effective_org_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_user_auth_does_not_support_effective_org(self):
        user_auth = MagicMock(spec=UserAuth)
        ctx = AuthUserContext(user_auth=user_auth)

        assert await ctx.get_effective_org_id() is None


class TestAuthUserContextCacheInvalidation:
    def test_discards_both_user_and_settings_views(self):
        user_auth = MagicMock(spec=UserAuth)
        ctx = AuthUserContext(user_auth=user_auth)
        ctx._user_info = MagicMock()
        ctx._resolved_user_info = MagicMock()

        ctx.invalidate_user_info_cache()

        assert ctx._user_info is None
        assert ctx._resolved_user_info is None
        user_auth.invalidate_user_settings_cache.assert_called_once_with()

    def test_user_auth_discards_persisted_and_resolved_settings(self):
        user_auth = MagicMock(spec=UserAuth)
        user_auth._settings = MagicMock()
        user_auth._resolved_settings = MagicMock()

        UserAuth.invalidate_user_settings_cache(user_auth)

        assert user_auth._settings is None
        assert user_auth._resolved_settings is None


class TestSpecifyUserContextEmail:
    """Admin contexts have no associated end-user email."""

    @pytest.mark.asyncio
    async def test_returns_none_for_admin_context(self):
        assert await ADMIN.get_user_email() is None

    @pytest.mark.asyncio
    async def test_returns_none_for_arbitrary_user_id(self):
        ctx = SpecifyUserContext(user_id='some-uuid')
        assert await ctx.get_user_email() is None
