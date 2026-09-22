from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.slack_team import SlackTeam
from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
@pytest.mark.budget_known_issue('OHE-3321')
async def test_failed_slack_delivery_retries_after_recovery(
    budget_adapter: BudgetTestAdapter,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 3)
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    settings = (
        await adapter.session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == adapter.org_id)
        )
    ).scalar_one()
    settings.slack_team_id = 'T_BUDGET_TEST'
    settings.slack_channel = 'C_BUDGET_TEST'
    adapter.session.add(
        SlackTeam(
            team_id=settings.slack_team_id, bot_access_token='test-only-no-network'
        )
    )
    threshold = OrgBudgetThreshold(
        org_id=adapter.org_id, percentage=20, email_enabled=False, slack_enabled=True
    )
    adapter.session.add(threshold)
    await adapter.session.commit()
    delivery = AsyncMock(
        side_effect=[RuntimeError('injected Slack outage'), {'ok': True}]
    )
    with patch('server.services.org_budget_service.AsyncWebClient') as client:
        client.return_value.chat_postMessage = delivery
        for _ in range(3):
            await adapter.service._maybe_send_alerts(
                adapter.org_id, settings, [threshold], 1, settings.cycle_start_at
            )
            await adapter.session.commit()
    assert delivery.await_count == 2, (
        'one failed attempt, one successful retry, then deduplicate'
    )
    await adapter.session.refresh(threshold)
    assert threshold.last_triggered_cycle_start == settings.cycle_start_at
