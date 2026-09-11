from __future__ import annotations

import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_disabling_budget_clears_team_and_member_caps(
    budget_adapter: BudgetTestAdapter,
) -> None:
    await budget_adapter.configure_budget(3.0, 1.0)
    await budget_adapter.disable_budget()
    financial_data = await budget_adapter.financial_data()

    assert financial_data['team_max_budget'] is None
    assert {member['max_budget'] for member in financial_data['members'].values()} == {
        None
    }
