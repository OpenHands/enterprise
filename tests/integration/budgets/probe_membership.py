from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from openhands.app_server.settings.settings_models import Settings
from server.services.org_invitation_service import OrgInvitationService
from storage.lite_llm_manager import LiteLlmManager
from storage.org import Org
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_store import OrgBudgetStore
from storage.org_invitation_store import OrgInvitationStore
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.user import User
from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.fixture
def provisioning_services(async_session_maker, monkeypatch):
    monkeypatch.delenv('LOCAL_DEPLOYMENT', raising=False)
    for module in (
        'storage.database',
        'storage.org_budget_provisioning',
        'storage.org_store',
        'storage.org_member_store',
        'storage.org_invitation_store',
        'storage.user_store',
    ):
        monkeypatch.setattr(f'{module}.a_session_maker', async_session_maker)
    with patch('storage.lite_llm_manager.TokenManager') as token_manager:
        token_manager.return_value.get_user_info_from_user_id = AsyncMock(
            side_effect=lambda user_id: {'email': f'{user_id}@example.invalid'}
        )
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize('org_enabled', [True, False])
@pytest.mark.parametrize(
    'mode',
    [
        'new-user',
        'invited-user',
        'key-refresh',
        'override-key-refresh',
        'override-new-user',
        'disabled-new-user',
    ],
)
async def test_provisioning_preserves_member_policy_before_first_request(
    budget_adapter: BudgetTestAdapter,
    provisioning_services,
    mode: str,
    org_enabled: bool,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 1)
    if not org_enabled:
        await adapter.disable_budget()
    existing_member = mode.endswith('key-refresh')
    user_id = adapter.user_ids[0] if existing_member else uuid4()
    if mode == 'override-key-refresh':
        await adapter.set_default_user_limit(3)
        await adapter.set_override(user_id, 1)
    if existing_member:
        assert (await adapter.send_request(user_id)).status_code == 200
        await adapter.wait_for_spend(1)
    if mode in {'override-new-user', 'disabled-new-user'}:
        adapter.session.add(User(id=user_id, current_org_id=adapter.org_id))
        await adapter.session.commit()
        await adapter.set_default_user_limit(3)
        await adapter.set_override(
            user_id,
            None if mode == 'disabled-new-user' else 1,
            disabled=mode == 'disabled-new-user',
        )
    if mode == 'invited-user':
        email = f'{user_id}@example.invalid'
        adapter.session.add(Org(id=user_id, name='Invitee personal workspace'))
        await adapter.session.flush()
        adapter.session.add(
            User(
                id=user_id,
                current_org_id=user_id,
                email=email,
                email_verified=True,
            )
        )
        owner = await adapter.session.get(
            OrgMember, (adapter.org_id, adapter.user_ids[0])
        )
        assert owner is not None
        await adapter.session.commit()
        assert await LiteLlmManager.create_user(email, str(user_id))
        invitation = await OrgInvitationStore.create_invitation(
            adapter.org_id, email, owner.role_id, adapter.user_ids[0]
        )
        accepted = await OrgInvitationService.accept_invitation(
            invitation.token, user_id
        )
        assert accepted.status == 'accepted'
        membership = await OrgMemberStore.get_org_member(adapter.org_id, user_id)
        assert membership is not None and membership.llm_api_key is not None
        new_key = membership.llm_api_key.get_secret_value()
    else:
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
        if mode in {'new-user', 'override-new-user', 'disabled-new-user'}:
            owner = await adapter.session.get(
                OrgMember, (adapter.org_id, adapter.user_ids[0])
            )
            assert owner is not None
            if mode == 'new-user':
                adapter.session.add(User(id=user_id, current_org_id=adapter.org_id))
                await adapter.session.commit()
            await OrgMemberStore.add_user_to_org(
                adapter.org_id, user_id, owner.role_id, new_key, status='active'
            )
        native = await adapter.financial_data()
        member = native['members'][str(user_id)]
        if mode == 'disabled-new-user':
            assert member['uses_shared_budget'] is True
            assert member['max_budget'] == (5 if org_enabled else None)
            assert (await adapter.send_request(user_id)).status_code == 200
            return
        assert member['max_budget'] == 1
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
            # A later sync must not treat already-consumed usage as the baseline.
            for _ in range(2):
                await adapter.run_maintenance()
                after = await adapter.financial_data()
                assert after['members'][str(user_id)]['max_budget'] == 1
                assert (await adapter.send_request(user_id)).status_code == 429
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


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['member-add', 'readback'])
async def test_failed_new_member_setup_issues_no_key_and_can_retry(
    budget_adapter: BudgetTestAdapter, provisioning_services, fault: str
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 1)
    user_id = str(uuid4())
    assert await LiteLlmManager.create_user(f'{user_id}@example.invalid', user_id)
    original_add = LiteLlmManager._add_user_to_team

    async def add_then_fail_readback(*args):
        await original_add(*args)
        await adapter.fail_next_management_call('/team/info')

    key = None
    try:
        if fault == 'member-add':
            await adapter.fail_next_management_call('/team/member_add')
        with (
            patch.object(
                LiteLlmManager,
                '_add_user_to_team',
                side_effect=add_then_fail_readback
                if fault == 'readback'
                else original_add,
            ),
            patch.object(LiteLlmManager, '_generate_key') as generate_key,
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await LiteLlmManager.create_entries(
                    str(adapter.org_id), user_id, Settings(), create_user=False
                )
            generate_key.assert_not_called()
        await adapter.reset_faults()
        settings = await LiteLlmManager.create_entries(
            str(adapter.org_id), user_id, Settings(), create_user=False
        )
        assert settings is not None and settings.agent_settings.llm.api_key
        key = settings.agent_settings.llm.api_key.get_secret_value()
        native = await adapter.financial_data()
        assert native['members'][user_id]['max_budget'] == 1
    finally:
        await adapter.reset_faults()
        if key:
            await LiteLlmManager.delete_key(key)
        await LiteLlmManager.delete_user(user_id)


@pytest.mark.asyncio
async def test_new_member_without_default_shares_only_the_organization_cap(
    budget_adapter: BudgetTestAdapter, provisioning_services
) -> None:
    adapter = budget_adapter
    await adapter.update_settings(
        enabled=True, monthly_limit=5, default_user_monthly_limit=None
    )
    user_id = str(uuid4())
    settings = await LiteLlmManager.create_entries(
        str(adapter.org_id), user_id, Settings(), create_user=True
    )
    assert settings is not None and settings.agent_settings.llm.api_key
    key = settings.agent_settings.llm.api_key.get_secret_value()
    try:
        member = (await adapter.financial_data())['members'][user_id]
        assert member['uses_shared_budget'] is True
        assert member['max_budget'] == 5
    finally:
        await LiteLlmManager.delete_key(key)
        await LiteLlmManager.delete_user(user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('keep_app_member', [True, False])
async def test_recreated_native_member_does_not_receive_old_cycle_baseline(
    budget_adapter: BudgetTestAdapter,
    provisioning_services,
    configured_litellm_manager,
    keep_app_member: bool,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 1)
    user_id = adapter.user_ids[0]
    store = OrgBudgetStore(adapter.session)
    settings = await store.get_settings(adapter.org_id)
    assert settings is not None
    settings.user_cycle_start_spend = {
        **settings.user_cycle_start_spend,
        str(user_id): 7,
    }
    await store.record_cycle_baselines(
        adapter.org_id,
        settings.cycle_start_at,
        {str(user_id): 7},
        source=OrgBudgetCycleBaseline.SOURCE_MEMBER_ADDED,
        observed_at=datetime.now(UTC),
        replace=True,
    )
    if not keep_app_member:
        await adapter.session.execute(
            delete(OrgMember).where(
                OrgMember.org_id == adapter.org_id, OrgMember.user_id == user_id
            )
        )
    await adapter.session.commit()
    async with httpx.AsyncClient(
        headers={'x-goog-api-key': configured_litellm_manager.master_key}
    ) as client:
        await LiteLlmManager._remove_user_from_team(
            client, str(user_id), str(adapter.org_id)
        )
    old_key = adapter.keys[user_id]
    new_key = None
    try:
        provisioned = await LiteLlmManager.create_entries(
            str(adapter.org_id), str(user_id), Settings(), create_user=False
        )
        assert provisioned is not None and provisioned.agent_settings.llm.api_key
        new_key = provisioned.agent_settings.llm.api_key.get_secret_value()
        adapter.keys[user_id] = new_key
        assert (await adapter.financial_data())['members'][str(user_id)][
            'max_budget'
        ] == 1
        await adapter.session.refresh(settings)
        assert settings.user_cycle_start_spend[str(user_id)] == 0
        assert (
            await store.get_cycle_baselines(adapter.org_id, settings.cycle_start_at)
        )[str(user_id)] == 0
        assert (await adapter.send_request(user_id)).status_code == 200
        await adapter.wait_for_spend(1)
        assert (await adapter.send_request(user_id)).status_code == 429
        if keep_app_member:
            await adapter.run_maintenance()
            assert (await adapter.financial_data())['members'][str(user_id)][
                'max_budget'
            ] == 1
            assert (await adapter.send_request(user_id)).status_code == 429
    finally:
        adapter.keys[user_id] = old_key
        if new_key:
            await LiteLlmManager.delete_key(new_key)
