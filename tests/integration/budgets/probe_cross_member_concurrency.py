from __future__ import annotations

import pytest

from tests.integration.budgets.adapter import FIXED_REQUEST_COST, BudgetTestAdapter


@pytest.mark.asyncio
async def test_cross_member_concurrency_preserves_org_cap_authority(
    budget_adapter: BudgetTestAdapter,
) -> None:
    await budget_adapter.configure_budget(
        organization_limit=1.0,
        default_user_limit=2.0,
    )

    responses = await budget_adapter.send_concurrent_requests(
        list(budget_adapter.user_ids)
    )
    successful_requests = sum(response.status_code == 200 for response in responses)
    financial_data = await budget_adapter.wait_for_spend(
        successful_requests * FIXED_REQUEST_COST
    )

    assert 1 <= successful_requests <= 2
    assert financial_data['team_spend'] <= 2.0
    assert financial_data['team_spend'] == sum(
        member['spend'] for member in financial_data['members'].values()
    )
    assert await budget_adapter.provider_calls() == successful_requests

    provider_calls = await budget_adapter.provider_calls()
    rejected = await budget_adapter.send_concurrent_requests(
        list(budget_adapter.user_ids)
    )
    assert all(response.status_code != 200 for response in rejected)
    assert await budget_adapter.provider_calls() == provider_calls
