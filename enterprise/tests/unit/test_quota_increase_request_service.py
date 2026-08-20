from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from server.services.quota_increase_request_service import (
    QuotaIncreaseRequestService,
)

USER_ID = str(uuid4())

ENV_DEFAULT = {'OH_DAILY_CONVERSATION_LIMIT': '20'}


def make_user(daily_conversation_limit=None):
    return SimpleNamespace(
        daily_conversation_limit=daily_conversation_limit,
        current_org_id=None,
        work_email=None,
    )


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
    session.scalar.return_value = make_user(daily_conversation_limit=20)
    service = QuotaIncreaseRequestService(session)
    with patch.dict('os.environ', ENV_DEFAULT):
        with pytest.raises(HTTPException) as exc_info:
            await service.create_request(USER_ID, 'user@acme.com', 201)
    assert exc_info.value.status_code == 400
    assert '10x' in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_request_caps_against_base_default_not_current_limit():
    """A previously granted increase must not raise the 10x ceiling.

    Regression test for self-service escalation: with a deployment default
    of 20 and a user already increased to 200, the ceiling stays at 200
    (10x the base default), not 2000 (10x the current limit).
    """
    session = AsyncMock()
    session.scalar.return_value = make_user(daily_conversation_limit=200)
    service = QuotaIncreaseRequestService(session)
    with patch.dict('os.environ', ENV_DEFAULT):
        with pytest.raises(HTTPException) as exc_info:
            await service.create_request(USER_ID, 'user@acme.com', 250)
    assert exc_info.value.status_code == 400
    assert '10x' in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_request_rejects_below_baseline():
    session = AsyncMock()
    session.scalar.return_value = make_user(daily_conversation_limit=20)
    service = QuotaIncreaseRequestService(session)
    with patch.dict('os.environ', ENV_DEFAULT):
        with pytest.raises(HTTPException) as exc_info:
            await service.create_request(USER_ID, 'user@acme.com', 10)
    assert exc_info.value.status_code == 400
    assert 'at least' in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_create_request_rejects_unlimited_user():
    """No deployment/org default configured means no increase flow."""
    session = AsyncMock()
    session.scalar.return_value = make_user(daily_conversation_limit=None)
    service = QuotaIncreaseRequestService(session)
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': ''}):
        with pytest.raises(HTTPException) as exc_info:
            await service.create_request(USER_ID, 'user@acme.com', 100)
    assert exc_info.value.status_code == 400
    assert 'daily limit is configured' in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_request_rejects_recent_duplicate_pending():
    session = AsyncMock()
    existing = SimpleNamespace(
        id=1,
        status='pending',
        user_id=USER_ID,
        work_email='old@acme.com',
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.scalar.side_effect = [make_user(daily_conversation_limit=20), existing]
    service = QuotaIncreaseRequestService(session)
    with patch.dict('os.environ', ENV_DEFAULT):
        with pytest.raises(HTTPException) as exc_info:
            await service.create_request(USER_ID, 'user@acme.com', 100)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_request_expires_stale_pending_and_creates_new():
    """A pending request older than the token TTL no longer blocks the user."""
    session = AsyncMock()
    session.add = Mock()
    stale = SimpleNamespace(
        id=1,
        status='pending',
        user_id=USER_ID,
        work_email='old@acme.com',
        created_at=datetime.now(UTC) - timedelta(hours=2),
        updated_at=datetime.now(UTC) - timedelta(hours=2),
    )
    user = make_user(daily_conversation_limit=20)
    session.scalar.side_effect = [user, stale]
    service = QuotaIncreaseRequestService(session)
    with patch.dict('os.environ', ENV_DEFAULT):
        result = await service.create_request(USER_ID, 'user@acme.com', 100)

    assert stale.status == 'expired'
    assert result.status == 'pending'
    assert result.requested_limit == 100
    assert result.baseline_limit == 20
    assert user.work_email == 'user@acme.com'
    session.add.assert_called_once()
    session.commit.assert_awaited()


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
    already_approved = SimpleNamespace(id=1, status='approved', requested_limit=100)
    session.scalar.return_value = already_approved
    service = QuotaIncreaseRequestService(session)
    result = await service.approve_request(1)
    assert result.status == 'approved'
    # Should not call commit for already-approved
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_reject_request_marks_pending_rejected():
    session = AsyncMock()
    now = datetime.now(UTC)
    request = SimpleNamespace(
        id=1,
        status='pending',
        created_at=now,
        updated_at=now,
    )
    session.scalar.return_value = request
    service = QuotaIncreaseRequestService(session)
    result = await service.reject_request(1)

    assert result.status == 'rejected'
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_reject_request_idempotent_and_refuses_approved():
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(id=1, status='rejected')
    service = QuotaIncreaseRequestService(session)
    result = await service.reject_request(1)
    assert result.status == 'rejected'
    session.commit.assert_not_called()

    session2 = AsyncMock()
    session2.scalar.return_value = SimpleNamespace(id=2, status='approved')
    with pytest.raises(HTTPException) as exc_info:
        await QuotaIncreaseRequestService(session2).reject_request(2)
    assert exc_info.value.status_code == 409
