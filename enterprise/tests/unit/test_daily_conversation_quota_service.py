from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
    QuotaStatus,
)

USER_ID = str(uuid4())


@pytest.mark.asyncio
async def test_get_status_unlimited():
    """When limit is None (unlimited), remaining is None and reset_at is next UTC midnight."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=None),
        None,  # no usage record for today
    ]

    with patch.dict('os.environ', {}, clear=True):
        service = DailyConversationQuotaService(session)
        result = await service.get_status(USER_ID)

    assert isinstance(result, QuotaStatus)
    assert result.daily_limit is None
    assert result.remaining is None
    assert result.reset_at.endswith('T00:00:00+00:00')


@pytest.mark.asyncio
async def test_get_status_with_limit_and_usage():
    """When limit is set and some conversations used, remaining is limit - used."""
    session = AsyncMock()

    # First scalar call: User lookup returns a user with limit 20
    # Second scalar call: DailyConversationUsage lookup returns count 5
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=20, current_org_id=None),
        SimpleNamespace(conversation_count=5),
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID)

    assert result.daily_limit == 20
    assert result.used_today == 5
    assert result.remaining == 15
    assert result.reset_at.endswith('T00:00:00+00:00')


@pytest.mark.asyncio
async def test_get_status_remaining_floor_zero():
    """Remaining never goes below zero even if usage exceeds limit."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=10, current_org_id=None),
        SimpleNamespace(conversation_count=12),
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID)

    assert result.daily_limit == 10
    assert result.used_today == 12
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_get_status_no_usage_today():
    """When no usage record exists for today, used_today is 0."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=20, current_org_id=None),
        None,
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID)

    assert result.daily_limit == 20
    assert result.used_today == 0
    assert result.remaining == 20
