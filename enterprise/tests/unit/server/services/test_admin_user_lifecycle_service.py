"""Tests for instance-level user lifecycle orchestration."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
    UserDeletionResult,
)
from sqlalchemy import select, text
from storage.daily_conversation_usage import DailyConversationUsage
from storage.feature_flag import (
    FeatureFlagRule,  # noqa: F401  # register table for in-memory schema
)
from storage.org import Org
from storage.quota_increase_request import QuotaIncreaseRequest
from storage.user import User


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


@pytest.mark.asyncio
async def test_delete_user_data_executes_sql_and_clears_quota_references(
    async_session_maker,
):
    target_id = uuid4()
    approver_id = uuid4()
    org_id = uuid4()
    now = datetime.now(UTC)
    org = Org(id=org_id, name='lifecycle-test')
    target = User(id=target_id, current_org_id=org_id, email='target@example.com')
    approver = User(id=approver_id, current_org_id=org_id, email='admin@example.com')

    async with async_session_maker() as session:
        session.add_all(
            [
                org,
                target,
                approver,
                DailyConversationUsage(
                    user_id=target_id,
                    usage_date=date.today(),
                    conversation_count=1,
                    created_at=now,
                    updated_at=now,
                ),
                QuotaIncreaseRequest(
                    user_id=target_id,
                    work_email='target@work.example',
                    baseline_limit=10,
                    requested_limit=20,
                    status=QuotaIncreaseRequest.STATUS_PENDING,
                    created_at=now,
                    updated_at=now,
                ),
                QuotaIncreaseRequest(
                    user_id=approver_id,
                    work_email='admin@work.example',
                    baseline_limit=10,
                    requested_limit=20,
                    status=QuotaIncreaseRequest.STATUS_APPROVED,
                    created_at=now,
                    updated_at=now,
                    approved_by_user_id=target_id,
                ),
            ]
        )
        await session.commit()
        for table, column in (
            ('user', 'id'),
            ('daily_conversation_usage', 'user_id'),
            ('quota_increase_request', 'user_id'),
            ('quota_increase_request', 'approved_by_user_id'),
        ):
            await session.execute(
                text(
                    f'UPDATE "{table}" SET {column} = :uuid WHERE {column} = :hex_uuid'
                ),
                {'uuid': str(target_id), 'hex_uuid': target_id.hex},
            )
        await session.commit()

    service = AdminUserLifecycleService(MagicMock())
    with (
        patch(
            'server.services.admin_user_lifecycle_service.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.services.admin_user_lifecycle_service.UserStore.get_user_by_id',
            AsyncMock(return_value=target),
        ),
        patch(
            'server.services.admin_user_lifecycle_service.OrgStore.delete_org_cascade',
            AsyncMock(),
        ) as delete_org,
    ):
        await service._delete_user_data(str(target_id))

    delete_org.assert_awaited_once_with(target_id, requester_user_id=str(target_id))
    async with async_session_maker() as session:
        assert await session.get(User, target_id) is None
        assert (
            await session.scalar(
                select(DailyConversationUsage).where(
                    DailyConversationUsage.user_id == target_id
                )
            )
            is None
        )
        requests = list(await session.scalars(select(QuotaIncreaseRequest)))
        assert len(requests) == 1
        assert requests[0].user_id == approver_id
        assert requests[0].approved_by_user_id is None
