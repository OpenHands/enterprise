from __future__ import annotations

import httpx
import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_alert_only_form_save_preserves_spend(
    budget_adapter: BudgetTestAdapter, budget_http: httpx.AsyncClient
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 3)
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    url = f'/api/organizations/{adapter.org_id}/budgets'
    before = (await budget_http.get(url)).json()
    response = await budget_http.patch(
        url,
        json={
            'enabled': True,
            'monthly_limit': 5,
            'reset_day': before['reset_day'],
            'thresholds': [
                {'percentage': 75, 'email_enabled': False, 'slack_enabled': False}
            ],
        },
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after['current_spend'] == before['current_spend'] == 1
    assert after['applied_team_max_budget'] == before['applied_team_max_budget']
    assert (await adapter.financial_data())['team_max_budget'] == 5


@pytest.mark.asyncio
async def test_disable_api_succeeds_and_retry_is_healthy(
    budget_adapter: BudgetTestAdapter, budget_http: httpx.AsyncClient
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 3)
    await adapter.set_override(adapter.user_ids[0], 2)
    url = f'/api/organizations/{adapter.org_id}/budgets'
    responses = [
        await budget_http.patch(url, json={'enabled': False}) for _ in range(2)
    ]
    readback = await budget_http.get(url)
    native = await adapter.financial_data()
    assert native['team_max_budget'] is None
    assert native['members'][str(adapter.user_ids[0])]['max_budget'] == 2
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert readback.status_code == 200
    assert readback.json()['enabled'] is False
    assert readback.json()['reconciliation_state'] in {'healthy', 'inactive'}
