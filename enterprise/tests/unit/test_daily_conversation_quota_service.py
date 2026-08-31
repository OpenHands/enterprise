from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from server.services.daily_conversation_quota_service import (
    QUOTA_INCREASE_REQUEST_URL,
    DailyConversationQuotaService,
    QuotaStatus,
)

USER_ID = str(uuid4())
ORG_ID = uuid4()


@pytest.mark.asyncio
async def test_get_status_unlimited():
    """When limit is None (unlimited), remaining is None and reset_at is next UTC midnight."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user: no override
        SimpleNamespace(daily_conversation_limit=None),  # org: no override
        None,  # no usage record for today
    ]

    with patch.dict('os.environ', {}, clear=True):
        service = DailyConversationQuotaService(session)
        result = await service.get_status(USER_ID, ORG_ID)

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
        SimpleNamespace(daily_conversation_limit=20),
        SimpleNamespace(conversation_count=5),
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID, ORG_ID)

    assert result.daily_limit == 20
    assert result.used_today == 5
    assert result.remaining == 15
    assert result.reset_at.endswith('T00:00:00+00:00')


@pytest.mark.asyncio
async def test_get_status_remaining_floor_zero():
    """Remaining never goes below zero even if usage exceeds limit."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=10),
        SimpleNamespace(conversation_count=12),
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID, ORG_ID)

    assert result.daily_limit == 10
    assert result.used_today == 12
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_get_status_no_usage_today():
    """When no usage record exists for today, used_today is 0."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=20),
        None,
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID, ORG_ID)

    assert result.daily_limit == 20
    assert result.used_today == 0
    assert result.remaining == 20


@pytest.mark.asyncio
async def test_get_status_resolves_org_limit_through_full_query_sequence():
    """The production path: user override NULL -> org override -> usage.

    Pins the real three-query sequence (user, org, usage) that every
    request takes, so an ordering regression in ``get_status`` is caught.
    """
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user: inherit
        SimpleNamespace(daily_conversation_limit=30),  # org: org-specific limit
        SimpleNamespace(conversation_count=4),  # usage today
    ]

    service = DailyConversationQuotaService(session)
    result = await service.get_status(USER_ID, ORG_ID)

    assert session.scalar.await_count == 3
    assert result.daily_limit == 30
    assert result.used_today == 4
    assert result.remaining == 26


@pytest.mark.asyncio
async def test_get_status_org_exemption_reports_unlimited():
    """An exempt org (-1) surfaces as unlimited rather than a negative limit."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user: inherit
        SimpleNamespace(daily_conversation_limit=-1),  # org: exempt
        SimpleNamespace(conversation_count=99),  # usage today
    ]

    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        result = await service.get_status(USER_ID, ORG_ID)

    assert result.daily_limit is None
    assert result.used_today == 99
    assert result.remaining is None


def test_limit_reached_includes_quota_request_links():
    error = DailyConversationQuotaService._limit_reached(20, 20, date.today())
    assert error.status_code == 429
    assert error.detail['code'] == 'daily_conversation_limit_reached'
    assert '/settings/quota' in error.detail['message']
    assert QUOTA_INCREASE_REQUEST_URL in error.detail['message']
