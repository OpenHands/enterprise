from __future__ import annotations

import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_sync_recovery_preserves_spend_and_reopens_requests(
    budget_adapter: BudgetTestAdapter,
) -> None:
    await budget_adapter.configure_budget(5.0, 3.0)
    first = await budget_adapter.send_request(budget_adapter.user_ids[0])
    assert first.status_code == 200, first.text
    await budget_adapter.wait_for_spend(1.0)

    await budget_adapter.fail_next_management_call('/team/member_update')
    degraded = await budget_adapter.set_default_user_limit(2.0)
    assert degraded['settings'].litellm_last_sync_status == 'error'

    await budget_adapter.reset_faults()
    await budget_adapter.run_maintenance()
    repaired = await budget_adapter.budget_state()
    before_request = await budget_adapter.financial_data()

    assert repaired['settings'].litellm_last_sync_status == 'success'
    assert before_request['team_spend'] == 1.0
    assert before_request['members'][str(budget_adapter.user_ids[0])]['spend'] == 1.0

    second = await budget_adapter.send_request(budget_adapter.user_ids[0])
    assert second.status_code == 200, second.text
    after_request = await budget_adapter.wait_for_spend(2.0)
    assert after_request['members'][str(budget_adapter.user_ids[0])]['spend'] == 2.0
