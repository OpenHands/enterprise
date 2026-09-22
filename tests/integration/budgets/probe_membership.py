from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openhands.app_server.settings.settings_models import Settings
from storage.lite_llm_manager import LiteLlmManager
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.user import User
from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'existing_member', [False, True], ids=['new-user', 'key-refresh']
)
async def test_provisioning_preserves_member_policy_before_first_request(
    budget_adapter: BudgetTestAdapter,
    async_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    existing_member: bool,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 1)
    user_id = adapter.user_ids[0] if existing_member else uuid4()
    if existing_member:
        assert (await adapter.send_request(user_id)).status_code == 200
        await adapter.wait_for_spend(1)
    monkeypatch.delenv('LOCAL_DEPLOYMENT', raising=False)
    monkeypatch.setattr('storage.database.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.org_store.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.org_member_store.a_session_maker', async_session_maker)
    with patch('storage.lite_llm_manager.TokenManager') as token_manager:
        token_manager.return_value.get_user_info_from_user_id = AsyncMock(
            return_value={'email': f'{user_id}@example.invalid'}
        )
        settings = await LiteLlmManager.create_entries(
            str(adapter.org_id),
            str(user_id),
            Settings(),
            create_user=not existing_member,
        )
    assert settings is not None
    secret = settings.agent_settings.llm.api_key
    assert secret is not None
    new_key = secret.get_secret_value()
    old_key = adapter.keys.get(user_id)
    adapter.keys[user_id] = new_key
    try:
        if not existing_member:
            owner = await adapter.session.get(
                OrgMember, (adapter.org_id, adapter.user_ids[0])
            )
            assert owner is not None
            adapter.session.add(User(id=user_id, current_org_id=adapter.org_id))
            await adapter.session.commit()
            await OrgMemberStore.add_user_to_org(
                adapter.org_id, user_id, owner.role_id, new_key, status='active'
            )
        native = await adapter.financial_data()
        member = native['members'][str(user_id)]
        response = await adapter.send_request(user_id)
        if existing_member:
            assert response.status_code in {401, 403, 429}, response.text
            assert member['spend'] == 1
        else:
            assert response.status_code == 200, response.text
            await adapter.wait_for_spend(1)
            second = await adapter.send_request(user_id)
            assert second.status_code in {401, 403, 429}, (
                f'second $1 request returned {second.status_code}; '
                f'default is $1 but native member cap is {member["max_budget"]}'
            )
        assert member['max_budget'] == 1, (
            'provisioning must not replace a member cap with the organization cap'
        )
    finally:
        await LiteLlmManager.delete_key(new_key)
        if old_key:
            adapter.keys[user_id] = old_key
        else:
            adapter.keys.pop(user_id, None)
            await LiteLlmManager.delete_user(str(user_id))
