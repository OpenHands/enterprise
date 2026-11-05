from __future__ import annotations

import pytest

from tests.integration.budgets.adapter import FIXED_REQUEST_COST, BudgetTestAdapter


@pytest.mark.parametrize('parallelism', [2, 4])
@pytest.mark.asyncio
async def test_same_member_concurrency_has_exact_accounting_and_bounded_overshoot(
    budget_adapter: BudgetTestAdapter,
    parallelism: int,
) -> None:
    await budget_adapter.configure_budget(
        organization_limit=1.0,
        default_user_limit=float(parallelism),
    )
    user_id = budget_adapter.user_ids[0]

    responses = await budget_adapter.send_concurrent_requests([user_id] * parallelism)
    successful_requests = sum(response.status_code == 200 for response in responses)
    financial_data = await budget_adapter.wait_for_spend(
        successful_requests * FIXED_REQUEST_COST
    )

    assert 1 <= successful_requests <= parallelism
    assert financial_data['team_spend'] <= 1.0 + (parallelism - 1) * FIXED_REQUEST_COST
    assert financial_data['members'][str(user_id)]['spend'] == (
        successful_requests * FIXED_REQUEST_COST
    )
    assert await budget_adapter.provider_calls() == successful_requests

    provider_calls = await budget_adapter.provider_calls()
    rejected = await budget_adapter.send_request(user_id)
    assert rejected.status_code != 200
    assert await budget_adapter.provider_calls() == provider_calls
