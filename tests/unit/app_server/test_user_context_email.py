"""Unit tests for ``UserContext.get_user_email()`` implementations.

These tests cover the change that uses the user's email (when available) as
the Laminar trace ``user_id`` so traces are immediately attributable in the
Laminar UI, while still falling back to the internal user id (e.g. in OSS
mode or for admin-scoped contexts).
"""

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


@pytest.mark.asyncio
async def test_profile_override_uses_one_snapshot_per_request():
    from openhands.app_server.settings.settings_models import Settings
    from openhands.sdk import LLM
    from openhands.sdk.settings import OpenHandsAgentSettings

    profile_id = str(uuid4())
    settings = Settings(
        active_agent_profile_id=profile_id,
        active_agent_profile_revision=1,
        agent_settings=OpenHandsAgentSettings(llm=LLM(model='test'), tools=[]),
    )
    settings._resolved_view = True
    user_auth = MagicMock(spec=UserAuth)
    user_auth.get_user_id = AsyncMock(return_value='user')
    user_auth.get_user_settings = AsyncMock(return_value=settings)
    context = AuthUserContext(user_auth=user_auth)
    first = await context.get_user_info(override_agent_profile_id=profile_id)
    settings.active_agent_profile_revision = 2
    second = await context.get_user_info(override_agent_profile_id=profile_id)
    assert first is second
    assert second.active_agent_profile_revision == 1
    assert second._resolved_view is True
    user_auth.get_user_settings.assert_awaited_once()
    context.invalidate_user_info_cache()
    third = await context.get_user_info(override_agent_profile_id=profile_id)
    assert third.active_agent_profile_revision == 2


@pytest.mark.asyncio
async def test_auth_user_context_provider_connections_default_to_empty():
    """OSS auth has no orgs, so a linked profile resolves with no connection."""
    from openhands.app_server.user.auth_user_context import AuthUserContext

    class _AuthWithoutConnections:
        pass

    context = AuthUserContext(user_auth=_AuthWithoutConnections())  # type: ignore[arg-type]

    assert (await context.get_provider_connections()).all() == []


@pytest.mark.asyncio
async def test_auth_user_context_delegates_provider_connections():
    from openhands.app_server.settings.provider_connections import (
        ProviderConnections,
    )
    from openhands.app_server.user.auth_user_context import AuthUserContext

    connections = ProviderConnections()

    class _SaasLikeAuth:
        async def get_provider_connections(self):
            return connections

    context = AuthUserContext(user_auth=_SaasLikeAuth())  # type: ignore[arg-type]

    assert await context.get_provider_connections() is connections
