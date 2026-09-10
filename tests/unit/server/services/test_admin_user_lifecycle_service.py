"""Tests for instance-level user lifecycle orchestration."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
)
from storage.daily_conversation_usage import DailyConversationUsage
from storage.org import Org
from storage.quota_increase_request import QuotaIncreaseRequest
from storage.role import Role
from storage.user import User


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
        session.add_all([Role(id=900, name='admin', rank=2), org, target, approver])
        # Committed first: the rows below are foreign keys onto ``user``, and
        # SQLAlchemy has no relationship to order the inserts for us.
        await session.commit()
        session.add_all(
            [
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
        service = AdminUserLifecycleService(session_factory=async_session_maker)
        await service._delete_user_data(str(target_id))

    delete_org.assert_awaited_once_with(
        target_id, requester_user_id=str(target_id), delete_account=True
    )
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
