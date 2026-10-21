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
@pytest.mark.parametrize(
    'failure_path', [None, '/team/update', '/team/member_update', '/team/info']
)
async def test_disable_api_succeeds_and_retry_is_healthy(
    budget_adapter: BudgetTestAdapter,
    budget_http: httpx.AsyncClient,
    failure_path: str | None,
) -> None:
    adapter = budget_adapter
    first_user, second_user = adapter.user_ids
    await adapter.configure_budget(2, 3)
    await adapter.set_override(first_user, 1)
    for user in adapter.user_ids:
        assert (await adapter.send_request(user)).status_code == 200
    await adapter.wait_for_spend(
        2, expected_member_spend={first_user: 1, second_user: 1}
    )
    url = f'/api/organizations/{adapter.org_id}/budgets'
    before = (await budget_http.get(url)).json()
    provider_calls = await adapter.provider_calls()
    for user in adapter.user_ids:
        assert (await adapter.send_request(user)).status_code in {401, 403, 429}
    assert await adapter.provider_calls() == provider_calls

    if failure_path:
        await adapter.fail_next_management_call(failure_path, count=20)
        failed = await budget_http.patch(url, json={'enabled': False})
        assert failed.status_code == 503, failed.text
        assert failed.json()['enabled'] is False
        assert failed.json()['reconciliation_state'] in {'degraded', 'failed'}
        assert (await adapter.send_request(second_user)).status_code in {401, 403, 429}
        assert await adapter.provider_calls() == provider_calls
        await adapter.reset_faults()

    for _ in range(2):
        response = await budget_http.patch(url, json={'enabled': False})
        assert response.status_code == 200, response.text
        after = response.json()
        assert after['enabled'] is False
        assert after['reconciliation_state'] == 'inactive'
        assert after['budget_policy_matches'] is True
        assert after['current_spend'] == before['current_spend'] == 2
        assert after['cycle_start_at'] == before['cycle_start_at']

    native = await adapter.financial_data()
    assert native['team_max_budget'] is None
    assert native['members'][str(first_user)]['max_budget'] == 1
    assert native['members'][str(second_user)]['max_budget'] == 3
    assert native['team_spend'] == 2
    assert (await adapter.send_request(first_user)).status_code in {401, 403, 429}
    assert await adapter.provider_calls() == provider_calls
    assert (await adapter.send_request(second_user)).status_code == 200
    assert await adapter.provider_calls() == provider_calls + 1
    await adapter.wait_for_spend(
        3, expected_member_spend={first_user: 1, second_user: 2}
    )
    readback = await budget_http.get(url)
    assert readback.status_code == 200
    assert readback.json()['enabled'] is False
    assert readback.json()['reconciliation_state'] == 'inactive'
    assert readback.json()['current_spend'] == 3
