"""Tests for instance-level user lifecycle orchestration."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
    UserDeletionResult,
)


@pytest.fixture
def user():
    value = MagicMock()
    value.id = uuid4()
    value.email = 'user@example.com'
    value.current_org_id = uuid4()
    value.is_disabled = False
    value.role_id = None
    return value


@pytest.mark.asyncio
async def test_disable_user_invalidates_keycloak_keys_and_offline_token(user):
    token_manager = MagicMock()
    token_manager.disable_keycloak_user = AsyncMock()
    service = AdminUserLifecycleService(token_manager)
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch.object(service, '_delete_api_keys', AsyncMock()) as delete_keys,
        patch.object(service, '_delete_offline_token', AsyncMock()) as delete_token,
        patch.object(service, '_set_disabled', AsyncMock()) as set_disabled,
    ):
        result = await service.disable_user(str(user.id))

    assert result.user_id == str(user.id)
    token_manager.disable_keycloak_user.assert_awaited_once_with(
        str(user.id), user.email
    )
    delete_keys.assert_awaited_once_with(str(user.id))
    delete_token.assert_awaited_once_with(str(user.id))
    set_disabled.assert_awaited_once_with(str(user.id), True)


@pytest.mark.asyncio
async def test_enable_user_updates_keycloak_and_local_state(user):
    token_manager = MagicMock()
    token_manager.enable_keycloak_user = AsyncMock()
    service = AdminUserLifecycleService(token_manager)
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch.object(service, '_set_disabled', AsyncMock()) as set_disabled,
    ):
        result = await service.enable_user(str(user.id))

    assert result.email == user.email
    token_manager.enable_keycloak_user.assert_awaited_once_with(
        str(user.id), user.email
    )
    set_disabled.assert_awaited_once_with(str(user.id), False)


@pytest.mark.asyncio
async def test_delete_user_runs_all_cleanup_steps(user):
    token_manager = MagicMock()
    token_manager.disable_keycloak_user = AsyncMock()
    token_manager.delete_keycloak_user = AsyncMock(return_value=True)
    service = AdminUserLifecycleService(token_manager)
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch.object(service, '_delete_api_keys', AsyncMock()) as delete_keys,
        patch.object(service, '_delete_offline_token', AsyncMock()) as delete_token,
        patch.object(service, '_set_disabled', AsyncMock()),
        patch.object(service, '_delete_user_data', AsyncMock()) as delete_data,
        patch(
            'server.services.admin_user_lifecycle_service.LiteLlmManager.delete_user',
            AsyncMock(),
        ) as delete_llm,
    ):
        result = await service.delete_user(str(user.id))

    assert result.user_id == str(user.id)
    assert isinstance(result, UserDeletionResult)
    assert result.cleanup_warnings == []
    token_manager.disable_keycloak_user.assert_awaited_once_with(
        str(user.id), user.email
    )
    delete_keys.assert_awaited_once_with(str(user.id))
    delete_token.assert_awaited_once_with(str(user.id))
    delete_data.assert_awaited_once_with(str(user.id))
    delete_llm.assert_awaited_once_with(str(user.id))
    token_manager.delete_keycloak_user.assert_awaited_once_with(str(user.id))


@pytest.mark.asyncio
async def test_delete_user_reports_external_cleanup_warnings(user):
    token_manager = MagicMock()
    token_manager.disable_keycloak_user = AsyncMock()
    token_manager.delete_keycloak_user = AsyncMock(return_value=False)
    service = AdminUserLifecycleService(token_manager)
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch.object(service, '_delete_api_keys', AsyncMock()),
        patch.object(service, '_delete_offline_token', AsyncMock()),
        patch.object(service, '_set_disabled', AsyncMock()),
        patch.object(service, '_delete_user_data', AsyncMock()),
        patch(
            'server.services.admin_user_lifecycle_service.LiteLlmManager.delete_user',
            AsyncMock(side_effect=httpx.ConnectError('litellm down')),
        ),
    ):
        result = await service.delete_user(str(user.id))

    assert result.cleanup_warnings == [
        'LiteLLM cleanup failed: litellm down',
        'Keycloak deletion failed or user already absent',
    ]


@pytest.mark.asyncio
async def test_disable_user_rejects_last_active_superadmin(user):
    user.role_id = 1
    service = AdminUserLifecycleService(MagicMock())
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch(
            'server.services.admin_user_lifecycle_service.UserStore.list_super_admins',
            AsyncMock(return_value=[user]),
        ),
    ):
        with pytest.raises(LastSuperAdminError):
            await service.disable_user(str(user.id))


@pytest.mark.asyncio
async def test_disable_user_allows_already_disabled_superadmin(user):
    user.role_id = 1
    user.is_disabled = True
    service = AdminUserLifecycleService(MagicMock())
    with patch(
        'server.services.admin_user_lifecycle_service.UserStore.list_super_admins',
        AsyncMock(return_value=[user]),
    ):
        await service._ensure_not_last_active_superadmin(user)


@pytest.mark.asyncio
async def test_delete_user_reports_litellm_http_failure(user):
    token_manager = MagicMock()
    token_manager.disable_keycloak_user = AsyncMock()
    token_manager.delete_keycloak_user = AsyncMock(return_value=True)
    service = AdminUserLifecycleService(token_manager)
    with (
        patch.object(service, 'get_user', AsyncMock(return_value=user)),
        patch.object(service, '_delete_api_keys', AsyncMock()),
        patch.object(service, '_delete_offline_token', AsyncMock()),
        patch.object(service, '_set_disabled', AsyncMock()),
        patch.object(service, '_delete_user_data', AsyncMock()),
        patch(
            'server.services.admin_user_lifecycle_service.LiteLlmManager.delete_user',
            AsyncMock(side_effect=httpx.ConnectError('litellm down')),
        ),
    ):
        result = await service.delete_user(str(user.id))

    assert result.cleanup_warnings == ['LiteLLM cleanup failed: litellm down']


@pytest.mark.asyncio
async def test_missing_user_is_noop():
    service = AdminUserLifecycleService(MagicMock())
    with patch.object(service, 'get_user', AsyncMock(return_value=None)):
        assert await service.disable_user(str(uuid4())) is None
        assert await service.enable_user(str(uuid4())) is None
        assert await service.delete_user(str(uuid4())) is None
