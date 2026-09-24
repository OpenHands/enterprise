from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from tests.integration.budgets.adapter import BudgetAdapterFactory, BudgetTestAdapter


@pytest.mark.parametrize(
    ('failure_path', 'failure_count', 'change'),
    [
        ('/team/update', 1, 'organization'),
        ('/team/update', 20, 'organization'),
        ('/team/member_update', 1, 'member'),
        ('/team/info', 10, 'readback'),
    ],
)
@pytest.mark.asyncio
async def test_unverified_budget_policy_fails_closed_before_provider(
    budget_adapter: BudgetTestAdapter,
    budget_adapter_factory: BudgetAdapterFactory,
    failure_path: str,
    failure_count: int,
    change: str,
    record_property: Callable[[str, object], None],
) -> None:
    initial = await budget_adapter.configure_budget(5.0, 3.0)
    assert initial['settings'].litellm_last_sync_status == 'success'
    other_org = await budget_adapter_factory.create()
    await other_org.configure_budget(5.0, 3.0)
    user_id = budget_adapter.user_ids[0]
    first = await budget_adapter.send_request(user_id)
    assert first.status_code == 200, first.text
    await budget_adapter.wait_for_spend(1.0, expected_member_spend={user_id: 1.0})
    original_key = budget_adapter.keys[user_id]

    await budget_adapter.fail_next_management_call(failure_path, count=failure_count)
    if change == 'organization':
        degraded = await budget_adapter.set_organization_limit(4.0)
    else:
        degraded = await budget_adapter.set_default_user_limit(2.0)
    assert degraded['settings'].litellm_last_sync_status == 'error'

    provider_calls = await budget_adapter.provider_calls()
    blocked_statuses = []
    for blocked_user in (user_id, budget_adapter.user_ids[1], user_id):
        response = await budget_adapter.send_request(blocked_user)
        assert response.status_code in {401, 403, 429, 503}, response.text
        assert await budget_adapter.provider_calls() == provider_calls
        blocked_statuses.append(response.status_code)
    record_property('blocked_statuses', blocked_statuses)
    record_property('provider_calls_while_blocked', 0)

    if failure_path == '/team/update' and failure_count > 1:
        await budget_adapter.run_maintenance()
        still_degraded = await budget_adapter.budget_state()
        assert still_degraded['settings'].litellm_last_sync_status == 'error'
        retry = await budget_adapter.send_request(user_id)
        assert retry.status_code in {401, 403, 429, 503}, retry.text
        assert await budget_adapter.provider_calls() == provider_calls
        record_property('blocked_after_failed_maintenance', retry.status_code)

    unrelated = await other_org.send_request(other_org.user_ids[0])
    assert unrelated.status_code == 200, unrelated.text
    assert await budget_adapter.provider_calls() == provider_calls + 1
    record_property('unrelated_organization_status', unrelated.status_code)

    await budget_adapter.reset_faults()
    await budget_adapter.run_maintenance()
    repaired = await budget_adapter.budget_state()
    assert repaired['settings'].litellm_last_sync_status == 'success'
    before_request = await budget_adapter.financial_data()
    assert before_request['team_spend'] == 1.0
    assert before_request['members'][str(user_id)]['spend'] == 1.0
    assert budget_adapter.keys[user_id] == original_key
    record_property('spend_preserved', before_request['team_spend'])

    recovered = await budget_adapter.send_request(user_id)
    assert recovered.status_code == 200, recovered.text
    record_property('same_key_recovery_status', recovered.status_code)
    after_request = await budget_adapter.wait_for_spend(
        2.0, expected_member_spend={user_id: 2.0}
    )
    assert after_request['members'][str(user_id)]['spend'] == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['organization', 'default', 'override', 'delete'])
@pytest.mark.parametrize('readback_available', [True, False])
async def test_edit_is_rejected_when_both_block_endpoints_fail(
    budget_adapter: BudgetTestAdapter,
    budget_http: httpx.AsyncClient,
    change: str,
    readback_available: bool,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5.0, 3.0)
    user = adapter.user_ids[0]
    await adapter.set_override(user, 2)
    first = await adapter.send_request(user)
    assert first.status_code == 200, first.text
    await adapter.wait_for_spend(1, expected_member_spend={user: 1})
    url = f'/api/organizations/{adapter.org_id}/budgets'
    before = (await budget_http.get(url)).json()

    await adapter.reset_faults()
    await adapter.fail_next_management_call('/team/update', count=20)
    await adapter.fail_next_management_call('/team/block', count=20)
    if not readback_available:
        await adapter.fail_next_management_call('/team/info', count=20)
    method, endpoint, payload = {
        'organization': (
            'PATCH',
            url,
            {
                'monthly_limit': 4,
                'reset_day': 15,
                'thresholds': [
                    {'percentage': 80, 'email_enabled': False, 'slack_enabled': False}
                ],
            },
        ),
        'default': ('PATCH', url, {'default_user_monthly_limit': 1}),
        'override': (
            'PUT',
            f'{url}/overrides/{user}',
            {'monthly_limit': 1, 'is_disabled': False},
        ),
        'delete': ('DELETE', f'{url}/overrides/{user}', None),
    }[change]
    response = await budget_http.request(method, endpoint, json=payload)
    assert response.status_code == 503, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'budget_change_rejected'
    assert detail['previous_policy_verified'] is readback_available
    async with httpx.AsyncClient() as client:
        paths = (await client.get(f'{adapter.proxy_url}/test/requests')).json()['paths']
    assert '/team/member_update' not in paths
    assert '/team/member_add' not in paths

    calls = await adapter.provider_calls()
    assert (await adapter.send_request(user)).status_code == 200
    assert await adapter.provider_calls() == calls + 1
    await adapter.reset_faults()
    native = await adapter.wait_for_spend(2, expected_member_spend={user: 2})
    assert native['team_max_budget'] == 5
    assert native['members'][str(user)]['max_budget'] == 2
    assert (await adapter.send_request(user)).status_code in {401, 403, 429}
    assert await adapter.provider_calls() == calls + 1
    after = (await budget_http.get(url)).json()
    for field in [
        'enabled',
        'monthly_limit',
        'default_user_monthly_limit',
        'reset_day',
        'cycle_start_at',
        'cycle_end_at',
        'thresholds',
    ]:
        assert after[field] == before[field], field
    assert after['current_spend'] == 2
    assert after['litellm_last_sync_status'] == 'error'

    recovered = await budget_http.request(method, endpoint, json=payload)
    assert recovered.status_code in {200, 204}, recovered.text
    after_retry = (await budget_http.get(url)).json()
    assert after_retry['current_spend'] == 2
    assert after_retry['cycle_start_at'] == before['cycle_start_at']
