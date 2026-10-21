from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from server.services.daily_sandbox_quota_service import (
    QUOTA_INCREASE_REQUEST_URL,
    DailySandboxQuotaService,
    QuotaStatus,
)

USER_ID = str(uuid4())


@pytest.mark.parametrize(
    ('env', 'expected'),
    [
        # New variable only.
        ({'OH_DAILY_SANDBOX_LIMIT': '7'}, 7),
        # Legacy variable only: prod keeps its 20 without a chart change.
        ({'OH_DAILY_CONVERSATION_LIMIT': '20'}, 20),
        # Both set: the new variable wins.
        ({'OH_DAILY_SANDBOX_LIMIT': '7', 'OH_DAILY_CONVERSATION_LIMIT': '20'}, 7),
        # Blank new variable means unset, so the legacy value still applies.
        ({'OH_DAILY_SANDBOX_LIMIT': '  ', 'OH_DAILY_CONVERSATION_LIMIT': '20'}, 20),
        # Nothing configured: unlimited.
        ({}, None),
    ],
)
def test_configured_daily_limit(env, expected):
    from server.services.daily_sandbox_quota_service import configured_daily_limit

    with patch.dict('os.environ', env, clear=True):
        assert configured_daily_limit() == expected


ORG_ID = uuid4()


@pytest.mark.asyncio
async def test_get_status_unlimited():
    """When limit is None (unlimited), remaining is None and reset_at is next UTC midnight."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_sandbox_limit=None),  # user: no override
        SimpleNamespace(daily_sandbox_limit=None),  # org: no override
        None,  # no usage record for today
    ]

    with patch.dict('os.environ', {}, clear=True):
        service = DailySandboxQuotaService(session)
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
    # Second scalar call: DailySandboxUsage lookup returns count 5
    session.scalar.side_effect = [
        SimpleNamespace(daily_sandbox_limit=20),
        SimpleNamespace(sandbox_count=5),
    ]

    service = DailySandboxQuotaService(session)
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
        SimpleNamespace(daily_sandbox_limit=10),
        SimpleNamespace(sandbox_count=12),
    ]

    service = DailySandboxQuotaService(session)
    result = await service.get_status(USER_ID, ORG_ID)

    assert result.daily_limit == 10
    assert result.used_today == 12
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_get_status_no_usage_today():
    """When no usage record exists for today, used_today is 0."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_sandbox_limit=20),
        None,
    ]

    service = DailySandboxQuotaService(session)
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
        SimpleNamespace(daily_sandbox_limit=None),  # user: inherit
        SimpleNamespace(daily_sandbox_limit=30),  # org: org-specific limit
        SimpleNamespace(sandbox_count=4),  # usage today
    ]

    service = DailySandboxQuotaService(session)
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
        SimpleNamespace(daily_sandbox_limit=None),  # user: inherit
        SimpleNamespace(daily_sandbox_limit=-1),  # org: exempt
        SimpleNamespace(sandbox_count=99),  # usage today
    ]

    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailySandboxQuotaService(session)
        result = await service.get_status(USER_ID, ORG_ID)

    assert result.daily_limit is None
    assert result.used_today == 99
    assert result.remaining is None


def test_limit_reached_includes_quota_request_links():
    error = DailySandboxQuotaService._limit_reached(20, 20, date.today())
    assert error.status_code == 429
    assert error.detail['code'] == 'daily_sandbox_limit_reached'
    assert '/settings/quota' in error.detail['message']
    assert error.detail['message'].endswith(QUOTA_INCREASE_REQUEST_URL)
    assert not error.detail['message'].endswith(f'{QUOTA_INCREASE_REQUEST_URL}.')
