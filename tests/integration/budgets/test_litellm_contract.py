from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_settings import OrgBudgetSettings


@pytest.mark.asyncio
async def test_enterprise_management_authentication(configured_litellm_manager):
    team_id = str(uuid4())
    try:
        await LiteLlmManager.create_team(
            team_alias='Budget property test',
            team_id=team_id,
            max_budget=2.0,
        )
        response = await LiteLlmManager.get_team(team_id)

        assert response is not None
        assert response['team_info']['team_id'] == team_id
        assert response['team_info']['max_budget'] == 2.0
    finally:
        await LiteLlmManager.delete_team(team_id)


@pytest.mark.asyncio
async def test_completion_has_deterministic_team_and_member_cost(
    configured_litellm_manager,
):
    environment = configured_litellm_manager
    team_id = str(uuid4())
    user_id = str(uuid4())
    key: str | None = None

    try:
        await LiteLlmManager.create_team(
            team_alias='Budget charging test',
            team_id=team_id,
            max_budget=10.0,
        )
        assert await LiteLlmManager.create_user(
            email=f'{user_id}@example.com', keycloak_user_id=user_id
        )
        await LiteLlmManager.add_user_to_team(
            keycloak_user_id=user_id,
            team_id=team_id,
            max_budget=10.0,
        )
        key = await LiteLlmManager.generate_key(
            keycloak_user_id=user_id,
            team_id=team_id,
            key_alias=f'budget-test-{user_id}',
            metadata={'test': True},
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f'{environment.direct_url}/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}'},
                json={
                    'model': 'budget-test-model',
                    'messages': [{'role': 'user', 'content': 'charge one unit'}],
                },
                timeout=30,
            )
        assert response.status_code == 200, response.text

        financial_data: dict = {}
        for _ in range(100):
            financial_data = await LiteLlmManager.get_team_members_financial_data(
                team_id
            )
            member = financial_data.get('members', {}).get(user_id, {})
            if financial_data.get('team_spend') == 1.0 and member.get('spend') == 1.0:
                break
            await asyncio.sleep(0.1)

        assert financial_data['team_spend'] == 1.0
        assert financial_data['members'][user_id]['spend'] == 1.0
        async with httpx.AsyncClient() as client:
            provider_response = await client.get(
                f'{environment.provider_url}/test/requests', timeout=5
            )
        assert provider_response.json() == {'calls': 1}
    finally:
        if key is not None:
            await LiteLlmManager.delete_key(key)
        await LiteLlmManager.delete_user(user_id)
        await LiteLlmManager.delete_team(team_id)


@pytest.mark.asyncio
async def test_partial_budget_sync_is_recorded_and_retry_converges(budget_adapter):
    initial = await budget_adapter.configure_budget(3.0, 1.0)
    assert initial['settings'].litellm_last_sync_status == 'success'

    await budget_adapter.fail_next_management_call('/team/member_update')
    partial = await budget_adapter.set_default_user_limit(2.0)

    assert partial['settings'].litellm_last_sync_status == 'error'
    assert 'user_update_failed' in partial['settings'].litellm_last_sync_error
    partial_financial_data = await budget_adapter.financial_data()
    partial_caps = {
        member['max_budget'] for member in partial_financial_data['members'].values()
    }
    assert partial_caps == {1.0, 2.0}

    await budget_adapter.reset_faults()
    await budget_adapter.run_maintenance()
    repaired = await budget_adapter.budget_state()
    repaired_financial_data = await budget_adapter.financial_data()

    assert repaired['settings'].litellm_last_sync_status == 'success'
    assert {
        member['max_budget'] for member in repaired_financial_data['members'].values()
    } == {2.0}


@pytest.mark.asyncio
async def test_known_member_missing_baseline_is_recovered_without_renewal(
    budget_adapter,
):
    await budget_adapter.configure_budget(100.0, 30.0)
    user_id = budget_adapter.user_ids[0]

    await LiteLlmManager.update_user_in_team(
        str(user_id),
        str(budget_adapter.org_id),
        max_budget=5.0,
    )
    settings = await budget_adapter.session.scalar(
        select(OrgBudgetSettings).where(
            OrgBudgetSettings.org_id == budget_adapter.org_id
        )
    )
    assert settings is not None
    settings.user_cycle_start_spend = {}
    settings.litellm_known_member_ids = [
        str(user_id) for user_id in budget_adapter.user_ids
    ]
    await budget_adapter.session.commit()

    await budget_adapter.run_maintenance()
    recovered_state = await budget_adapter.budget_state()
    recovered_financial_data = await budget_adapter.financial_data()

    assert recovered_state['settings'].litellm_last_sync_status == 'success'
    assert recovered_state['settings'].user_cycle_start_spend == {
        str(user_id): 0.0 for user_id in budget_adapter.user_ids
    }
    assert recovered_financial_data['members'][str(user_id)]['max_budget'] == 30.0

    await budget_adapter.run_maintenance()
    later_state = await budget_adapter.budget_state()
    later_financial_data = await budget_adapter.financial_data()

    assert later_state['settings'].user_cycle_start_spend == {
        str(user_id): 0.0 for user_id in budget_adapter.user_ids
    }
    assert later_financial_data['members'][str(user_id)]['max_budget'] == 30.0
