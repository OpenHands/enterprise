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


@pytest.mark.asyncio
@pytest.mark.parametrize('edit', ['default', 'override', 'delete', 'remove_limit'])
async def test_disabled_org_edits_change_existing_key_enforcement(
    budget_adapter: BudgetTestAdapter, edit: str
) -> None:
    adapter = budget_adapter
    user = adapter.user_ids[0]
    await adapter.configure_budget(5, 2)
    if edit == 'delete':
        await adapter.set_override(user, 1)
    await adapter.disable_budget()
    assert (await adapter.send_request(user)).status_code == 200
    await adapter.wait_for_spend(1)
    if edit == 'default':
        await adapter.set_default_user_limit(1)
    elif edit == 'override':
        await adapter.set_override(user, 1)
    elif edit == 'delete':
        await adapter.delete_override(user)
    else:
        await adapter.update_settings(default_user_monthly_limit=None)
    state = await adapter.budget_state()
    assert state['budget_policy_matches'] is True
    assert state['current_spend'] == 1
    native = await adapter.financial_data()
    assert native['team_max_budget'] is None
    cap = native['members'][str(user)]['max_budget']
    assert cap == (2 if edit == 'delete' else None if edit == 'remove_limit' else 1)
    before = await adapter.provider_calls()
    response = await adapter.send_request(user)
    if edit in {'default', 'override'}:
        assert response.status_code in {401, 403, 429}, response.text
        assert await adapter.provider_calls() == before
    else:
        assert response.status_code == 200, response.text
        assert await adapter.provider_calls() == before + 1
