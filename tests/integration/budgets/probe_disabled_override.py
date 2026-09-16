from __future__ import annotations

from tests.integration.budgets.adapter import BudgetAdapterFactory
from tests.integration.budgets.test_org_budget_state_machine import (
    run_budget_state_machine,
)


def test_disabled_overrides_refine_budget_model(
    budget_adapter_factory: BudgetAdapterFactory,
) -> None:
    run_budget_state_machine(
        budget_adapter_factory,
        allow_requests=False,
        allow_disabled_overrides=True,
    )
