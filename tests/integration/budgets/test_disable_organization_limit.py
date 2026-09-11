from __future__ import annotations

import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_disabling_organization_limit_preserves_member_enforcement(
    budget_adapter: BudgetTestAdapter,
) -> None:
    await budget_adapter.configure_budget(3.0, 1.0)
    await budget_adapter.disable_budget()
    financial_data = await budget_adapter.financial_data()

    assert financial_data['team_max_budget'] is None
    assert {member['max_budget'] for member in financial_data['members'].values()} == {
        1.0
    }

    first = await budget_adapter.send_request(budget_adapter.user_ids[0])
    assert first.status_code == 200, first.text
    await budget_adapter.wait_for_spend(1.0)

    provider_calls = await budget_adapter.provider_calls()
    second = await budget_adapter.send_request(budget_adapter.user_ids[0])
    assert second.status_code in {401, 403, 429}, second.text
    assert await budget_adapter.provider_calls() == provider_calls
