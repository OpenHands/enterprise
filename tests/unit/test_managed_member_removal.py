"""Access revocation retains enforcement history and rejoining does not reanchor."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from server.services.managed_budget_service import ManagedBudgetService
from storage.budget_control import BudgetControlConflict, BudgetWriteDenied
from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from tests.unit.test_managed_member_admission import admission as admission_fixture
from tests.unit.test_managed_member_admission import adoption as adoption_fixture

admission = admission_fixture
adoption = adoption_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure', [None, 'before_revoke', 'after_revoke', 'after_budget']
)
async def test_removal_revokes_credentials_without_deleting_spending_history(
    admission, async_engine, async_session_maker, failure
):
    org_id, original_id, user_id, role_id, proxy, native = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    proxy.state['member_counters'][str(user_id)]['spend'] = 6
    before_counter = deepcopy(proxy.state['member_counters'][str(user_id)])
    native.failure = failure
    if failure == 'after_budget':
        proxy.lose_next_response = True
    assert await OrgMemberStore.remove_user_from_org(org_id, user_id)
    assert await OrgMemberStore.get_org_member(org_id, user_id) is None
    assert await OrgMemberStore.is_member_revocation_pending(org_id, user_id) == (
        failure is not None
    )
    assert not await OrgMemberStore.is_member_revocation_pending(org_id, original_id)
    service = ManagedBudgetService(async_engine)
    result = await service.maintain(org_id)
    assert result['status'] in {'healthy', 'applied'}, result
    assert not await OrgMemberStore.is_member_revocation_pending(org_id, user_id)
    assert native.keys == []
    assert proxy.state['member_counters'][str(user_id)] == before_counter
    assert proxy.state['members'][str(user_id)]['max_budget'] == 0
    assert proxy.state['members'][original_id]['max_budget'] == 22
    assert proxy.state['team_max_budget'] == 140
    assert all(path != '/team/member_delete' for path, _ in native.calls)
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.user_cycle_start_spend[str(user_id)] == 0
        operation = await session.scalar(
            select(OrgBudgetOperation).where(OrgBudgetOperation.generation == 3)
        )
        assert operation.plan['inactive_member_ids'] == [str(user_id)]
        assert operation.status == 'applied'
        assert operation.plan['expected_member_caps'][str(user_id)] == 0


@pytest.mark.asyncio
async def test_rejoin_uses_old_baseline_and_new_credential_epoch(
    admission, async_session_maker
):
    org_id, _, user_id, role_id, proxy, native = admission
    first = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    original_key = first.llm_api_key.get_secret_value()
    proxy.state['member_counters'][str(user_id)]['spend'] = 6
    assert await OrgMemberStore.remove_user_from_org(org_id, user_id)
    second = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    assert second.managed_llm_key_ownership_version == 1
    assert second.llm_api_key.get_secret_value() != original_key
    assert proxy.state['members'][str(user_id)]['max_budget'] == 10
    assert proxy.state['member_counters'][str(user_id)]['spend'] == 6
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.user_cycle_start_spend[str(user_id)] == 0
        operation = await session.scalar(
            select(OrgBudgetOperation).where(OrgBudgetOperation.generation == 4)
        )
        assert operation.plan['inactive_member_ids'] == []
        assert operation.plan['member_credential_epochs'][str(user_id)]
    assert len([path for path, _ in native.calls if path == '/team/member_add']) == 1


@pytest.mark.asyncio
async def test_renewal_keeps_removed_member_denied(
    admission, async_engine, async_session_maker
):
    org_id, _, user_id, role_id, proxy, _ = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    assert await OrgMemberStore.remove_user_from_org(org_id, user_id)
    async with async_session_maker() as session:
        now = (
            await session.scalar(select(OrgBudgetSettings))
        ).cycle_end_at + timedelta(seconds=1)
    result = await ManagedBudgetService(async_engine).maintain(org_id, now=now)
    assert result['status'] == 'applied', result
    assert result['cycle_rolled']
    assert proxy.state['members'][str(user_id)]['max_budget'] == 0


@pytest.mark.asyncio
async def test_native_delete_helper_cannot_destroy_managed_counter(admission):
    org_id, _, user_id, role_id, _, native = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    with pytest.raises(BudgetWriteDenied, match='journaled member removal'):
        await LiteLlmManager.remove_user_from_team(str(user_id), str(org_id))
    assert all(path != '/team/member_delete' for path, _ in native.calls)


@pytest.mark.asyncio
async def test_removal_intent_and_app_access_revocation_commit_before_native_effect(
    admission, async_session_maker
):
    org_id, _, user_id, role_id, proxy, _ = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    write = proxy.write
    writes = []

    async def verify_commit(org, operation_id, path, body):
        async with async_session_maker() as session:
            assert (
                await session.get(OrgMember, {'org_id': org_id, 'user_id': user_id})
                is None
            )
            operation = await session.get(OrgBudgetOperation, operation_id)
            assert operation.status == 'pending'
            assert operation.plan['revoked_member_ids'] == [str(user_id)]
        writes.append((path, body))
        await write(org, operation_id, path, body)

    with patch(
        'storage.lite_llm_manager.LiteLlmManager.apply_budget_write', verify_commit
    ):
        assert await OrgMemberStore.remove_user_from_org(org_id, user_id)
    assert len(writes) == 1
    assert writes[0][0] == '/team/member_update'
    assert writes[0][1]['max_budget_in_team'] == 0


@pytest.mark.asyncio
async def test_failed_removal_intent_commit_preserves_membership_and_native_policy(
    admission, async_session_maker
):
    org_id, _, user_id, role_id, proxy, native = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    original = deepcopy(proxy.state)
    native.calls.clear()

    def fail_commit(session):
        if any(
            isinstance(row, OrgBudgetOperation)
            and row.plan.get('revoked_member_ids') == [str(user_id)]
            for row in session.new
        ):
            raise RuntimeError('removal intent commit failed')

    event.listen(Session, 'before_commit', fail_commit)
    try:
        with pytest.raises(RuntimeError, match='intent commit failed'):
            await OrgMemberStore.remove_user_from_org(org_id, user_id)
    finally:
        event.remove(Session, 'before_commit', fail_commit)
    assert await OrgMemberStore.get_org_member(org_id, user_id) is not None
    assert proxy.state == original
    assert all(body is None for _, body in native.calls)


@pytest.mark.asyncio
async def test_handoff_cannot_abandon_security_revocation(admission, async_engine):
    org_id, _, user_id, role_id, _, native = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    native.failure = 'before_revoke'
    assert await OrgMemberStore.remove_user_from_org(org_id, user_id)
    service = ManagedBudgetService(async_engine)
    with pytest.raises(BudgetControlConflict, match='member revocation'):
        await service.hand_off(org_id, 'admin')
    assert (await service.maintain(org_id))['status'] == 'applied'
    assert (await service.hand_off(org_id, 'admin'))['control_mode'] == 'external'
