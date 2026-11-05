from __future__ import annotations

import httpx
import pytest

from tests.integration.budgets.adapter import BudgetAdapterFactory, BudgetTestAdapter
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


@pytest.mark.asyncio
@pytest.mark.parametrize('individual_override', [False, True])
async def test_disabling_member_limit_restores_requests_without_refilling_spend(
    budget_adapter: BudgetTestAdapter,
    budget_http: httpx.AsyncClient,
    individual_override: bool,
) -> None:
    adapter = budget_adapter
    user, other_user = adapter.user_ids
    await adapter.configure_budget(3, 1)
    if individual_override:
        await adapter.set_override(user, 1)
    assert (await adapter.send_request(user)).status_code == 200
    before = await adapter.wait_for_spend(1)
    assert (await adapter.send_request(user)).status_code == 429

    endpoint = f'/api/organizations/{adapter.org_id}/budgets/overrides/{user}'
    response = await budget_http.put(
        endpoint, json={'monthly_limit': None, 'is_disabled': True}
    )
    assert response.status_code == 200, response.text
    assert response.json()['is_disabled'] is True
    assert response.json()['reconciliation_state'] == 'healthy'
    native = await adapter.financial_data()
    assert native['team_spend'] == before['team_spend']
    assert (
        native['members'][str(user)]['spend'] == before['members'][str(user)]['spend']
    )
    assert native['members'][str(other_user)] == before['members'][str(other_user)]
    assert native['team_max_budget'] == 3
    assert native['members'][str(user)]['uses_shared_budget'] is True
    assert (await adapter.send_request(user)).status_code == 200
    await adapter.wait_for_spend(2)
    await adapter.run_maintenance()

    # Restoring the inherited limit must count the spend already incurred.
    response = await budget_http.delete(endpoint)
    assert response.status_code == 204
    assert (await adapter.send_request(user)).status_code == 429
    response = await budget_http.put(
        endpoint, json={'monthly_limit': None, 'is_disabled': True}
    )
    assert response.status_code == 200, response.text
    await adapter.run_maintenance()
    assert (await adapter.send_request(user)).status_code == 200
    await adapter.wait_for_spend(3)
    calls = await adapter.provider_calls()
    assert (await adapter.send_request(user)).status_code == 429
    assert await adapter.provider_calls() == calls
