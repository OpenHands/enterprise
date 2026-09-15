"""Regression coverage for truthful financial reads and current-cycle alerts."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from server.routes import orgs
from server.services.org_budget_service import OrgBudgetService
from server.services.org_member_financial_service import OrgMemberFinancialService
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_threshold import OrgBudgetThreshold


@pytest.mark.asyncio
async def test_financial_route_preserves_unavailable_status():
    with patch.object(
        OrgMemberFinancialService,
        'get_org_members_financial_data',
        AsyncMock(side_effect=HTTPException(503, 'Financial data unavailable')),
    ):
        with pytest.raises(HTTPException) as exc:
            await orgs.get_org_members_financial(
                org_id=uuid4(), user_id='admin', page_id=None, limit=10, email=None
            )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', [401, 403, 429, 500, 503])
async def test_failed_financial_http_read_never_returns_balances(status_code):
    error = httpx.HTTPStatusError(
        'proxy failure',
        request=httpx.Request('GET', 'https://litellm.invalid/team/info'),
        response=httpx.Response(status_code),
    )
    with (
        patch(
            'storage.org_member_store.OrgMemberStore.get_org_members_paginated',
            AsyncMock(return_value=([object()], False)),
        ),
        patch(
            'storage.lite_llm_manager.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(side_effect=error),
        ),
    ):
        expected = httpx.HTTPStatusError if status_code in (401, 403) else HTTPException
        with pytest.raises(expected):
            await OrgMemberFinancialService.get_org_members_financial_data(uuid4())


@pytest.mark.asyncio
async def test_alert_uses_current_allowance_not_future_monthly_limit(
    async_session_maker,
):
    now = datetime.now(UTC)
    settings = OrgBudgetSettings(
        org_id=uuid4(),
        control_mode='managed',
        enabled=True,
        monthly_limit=700,
        cycle_allowance=100,
        cycle_end_at=now,
    )
    threshold = OrgBudgetThreshold(
        percentage=80, email_enabled=True, slack_enabled=False
    )
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        with patch.object(service, '_send_alerts', AsyncMock()) as send:
            await service._maybe_send_alerts(
                settings.org_id, settings, [threshold], 85, now
            )
            await service._maybe_send_alerts(
                settings.org_id, settings, [threshold], 90, now
            )
        send.assert_awaited_once_with(settings.org_id, settings, threshold, 85, 85.0)
