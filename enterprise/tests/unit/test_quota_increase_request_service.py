from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from server.services.quota_increase_request_service import (
    QuotaIncreaseRequestService,
)

USER_ID = str(uuid4())


@pytest.mark.asyncio
async def test_create_request_rejects_free_email():
    session = AsyncMock()
    service = QuotaIncreaseRequestService(session)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_request(USER_ID, 'user@gmail.com', 200)
    assert exc_info.value.status_code == 400
    assert 'work email' in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_create_request_rejects_too_high():
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(
        daily_conversation_limit=20, work_email=None
    )
    service = QuotaIncreaseRequestService(session)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_request(USER_ID, 'user@acme.com', 201)
    assert exc_info.value.status_code == 400
    assert '10x' in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_request_rejects_below_baseline():
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(
        daily_conversation_limit=20, work_email=None
    )
    service = QuotaIncreaseRequestService(session)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_request(USER_ID, 'user@acme.com', 10)
    assert exc_info.value.status_code == 400
    assert 'at least' in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_create_request_rejects_duplicate_pending():
    session = AsyncMock()
    existing = SimpleNamespace(
        id=1, status='pending', user_id=USER_ID, work_email='old@acme.com'
    )
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=20, work_email=None),
        existing,
    ]
    service = QuotaIncreaseRequestService(session)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_request(USER_ID, 'user@acme.com', 100)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_request_applies_limit():
    session = AsyncMock()
    now = datetime.now(UTC)
    request = SimpleNamespace(
        id=1,
        user_id=uuid4(),
        work_email='user@acme.com',
        baseline_limit=20,
        requested_limit=100,
        reason=None,
        status='pending',
        created_at=now,
        updated_at=now,
        approved_at=None,
        approved_by_user_id=None,
    )
    user = SimpleNamespace(
        id=request.user_id,
        daily_conversation_limit=20,
        work_email=None,
        work_email_verified_at=None,
    )
    session.scalar.side_effect = [request, user]
    service = QuotaIncreaseRequestService(session)
    result = await service.approve_request(1, approved_by_user_id=str(uuid4()))

    assert result.status == 'approved'
    assert user.daily_conversation_limit == 100
    assert user.work_email_verified_at is not None


@pytest.mark.asyncio
async def test_approve_request_idempotent():
    session = AsyncMock()
    already_approved = SimpleNamespace(
        id=1, status='approved', requested_limit=100
    )
    session.scalar.return_value = already_approved
    service = QuotaIncreaseRequestService(session)
    result = await service.approve_request(1)
    assert result.status == 'approved'
    # Should not call commit for already-approved
    session.commit.assert_not_called()
