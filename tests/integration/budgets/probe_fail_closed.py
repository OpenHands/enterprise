from __future__ import annotations

from collections.abc import Callable

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
@pytest.mark.budget_known_issue('OHE-3268')
async def test_unverified_policy_fails_closed_when_both_block_endpoints_fail(
    budget_adapter: BudgetTestAdapter,
) -> None:
    await budget_adapter.configure_budget(5.0, 3.0)
    first = await budget_adapter.send_request(budget_adapter.user_ids[0])
    assert first.status_code == 200, first.text
    await budget_adapter.wait_for_spend(1.0)

    await budget_adapter.fail_next_management_call('/team/update', count=20)
    await budget_adapter.fail_next_management_call('/team/block', count=20)
    degraded = await budget_adapter.set_organization_limit(4.0)
    assert degraded['settings'].litellm_last_sync_status == 'error'
    assert 'admission_fallback_failed' in degraded['settings'].litellm_last_sync_error

    provider_calls = await budget_adapter.provider_calls()
    response = await budget_adapter.send_request(budget_adapter.user_ids[0])
    assert response.status_code in {401, 403, 429, 503}, response.text
    assert await budget_adapter.provider_calls() == provider_calls
