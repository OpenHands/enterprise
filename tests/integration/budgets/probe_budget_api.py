from __future__ import annotations

import httpx
import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('monthly_limit', 'threshold'),
    [(5, 75), (5, None), (6, None), (1, None)],
    ids=['alert-only', 'unchanged-save', 'raise-limit', 'lower-limit'],
)
async def test_budget_form_save_preserves_spend(
    budget_adapter: BudgetTestAdapter,
    budget_http: httpx.AsyncClient,
    monthly_limit: int,
    threshold: int | None,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 3)
    await adapter.set_override(adapter.user_ids[0], 2)
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    url = f'/api/organizations/{adapter.org_id}/budgets'
    before = (await budget_http.get(url)).json()
    native_before = await adapter.financial_data()
    payload = {
        'enabled': True,
        'monthly_limit': monthly_limit,
        'reset_day': before['reset_day'],
        'slack_channel': None,
        'thresholds': [
            {
                field: item[field]
                for field in ('percentage', 'email_enabled', 'slack_enabled')
            }
            for item in before['thresholds']
        ],
    }
    if threshold is not None:
        payload['thresholds'] = [
            {'percentage': threshold, 'email_enabled': False, 'slack_enabled': False}
        ]

    for _ in range(2):
        response = await budget_http.patch(url, json=payload)
        assert response.status_code == 200, response.text
        after = response.json()
        native = await adapter.financial_data()
        assert after['current_spend'] == before['current_spend'] == 1
        assert after['cycle_start_at'] == before['cycle_start_at']
        assert native['team_spend'] == native_before['team_spend'] == 1
        assert native['team_max_budget'] == monthly_limit
        for user_id in adapter.user_ids:
            member = str(user_id)
            assert (
                native['members'][member]['max_budget']
                == (native_before['members'][member]['max_budget'])
            )

    if monthly_limit == 1:
        provider_calls = await adapter.provider_calls()
        response = await adapter.send_request(adapter.user_ids[0])
        assert response.status_code in {401, 403, 429}, response.text
        assert await adapter.provider_calls() == provider_calls


@pytest.mark.asyncio
@pytest.mark.budget_known_issue('OHE-3319')
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
