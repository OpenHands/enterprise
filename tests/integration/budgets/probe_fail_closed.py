from __future__ import annotations

import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.parametrize(
    ('failure_path', 'failure_count', 'change'),
    [
        ('/team/update', 1, 'organization'),
        ('/team/member_update', 1, 'member'),
        ('/team/info', 10, 'readback'),
    ],
)
@pytest.mark.asyncio
async def test_unverified_budget_policy_fails_closed_before_provider(
    budget_adapter: BudgetTestAdapter,
    failure_path: str,
    failure_count: int,
    change: str,
) -> None:
    initial = await budget_adapter.configure_budget(3.0, 1.0)
    assert initial['settings'].litellm_last_sync_status == 'success'

    await budget_adapter.fail_next_management_call(failure_path, count=failure_count)
    if change == 'organization':
        degraded = await budget_adapter.set_organization_limit(2.0)
    else:
        degraded = await budget_adapter.set_default_user_limit(2.0)
    assert degraded['settings'].litellm_last_sync_status == 'error'

    provider_calls = await budget_adapter.provider_calls()
    response = await budget_adapter.send_request(budget_adapter.user_ids[0])

    assert response.status_code in {401, 403, 429, 503}, response.text
    assert await budget_adapter.provider_calls() == provider_calls
