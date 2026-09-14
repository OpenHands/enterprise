"""Edits and renewals must reuse durable intent across workers and retries."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from server.services.managed_budget_service import (
    ManagedBudgetService,
    ManagedBudgetUpdate,
)
from server.services.org_budget_service import OrgBudgetService
from storage.budget_control import BudgetControlConflict, BudgetWriteDenied
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from tests.unit.test_budget_adoption_service import adoption as adoption_fixture

adoption = adoption_fixture


async def adopt_zero(adoption, *, operator_blocked=False):
    org_id, user_id, service, request, proxy = adoption
    proxy.state['control_policy']['team']['blocked'] = operator_blocked
    proxy.state['team_blocked'] = operator_blocked
    preview = await service.preview(org_id)
    request = request.model_copy(
        update={
            'current_team_allowance': 0,
            'preview_fingerprint': preview['fingerprint'],
        }
    )
    result = await service.confirm(org_id, 'admin', request)
    assert result['status'] == 'applied', result
    return org_id, user_id, proxy


def policy(**changes):
    values = dict(
        idempotency_key='edit-1',
        expected_generation=1,
        enabled=True,
        current_cycle_team_allowance=200,
        current_cycle_default_member_allowance=20,
        future_monthly_limit=900,
        future_default_member_limit=90,
        reset_day=15,
    )
    return ManagedBudgetUpdate(**(values | changes))


@pytest.fixture
async def managed(adoption, async_engine):
    org_id, user_id, service, request, proxy = adoption
    assert (await service.confirm(org_id, 'admin', request))['status'] == 'applied'
    yield org_id, user_id, ManagedBudgetService(async_engine), proxy


@pytest.mark.asyncio
async def test_policy_edit_does_not_reanchor_current_cycle(
    managed, async_session_maker
):
    org_id, user_id, service, proxy = managed
    proxy.state['team_spend'] = 90
    proxy.state['member_counters'][user_id]['spend'] = 20
    result = await service.update(org_id, 'admin', policy())
    assert result['status'] == 'applied', result
    assert proxy.state['team_max_budget'] == 240
    assert proxy.state['members'][user_id]['max_budget'] == 32
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.cycle_start_spend == 40
        assert settings.user_cycle_start_spend[user_id] == 12
        assert settings.cycle_allowance == 200
        assert settings.monthly_limit == 900
        # Changing future reset policy does not restart today's allowance.
        assert settings.cycle_end_at.day == 1
        assert settings.reset_day == 15


@pytest.mark.asyncio
async def test_override_write_reports_pending_until_litellm_is_verified(managed):
    org_id, user_id, service, proxy = managed
    proxy.lose_next_response = True
    first = await service.update(org_id, 'admin', policy())
    assert first['status'] == 'pending'
    proxy.state['team_spend'] = 100
    proxy.state['member_counters'][user_id]['spend'] = 25
    second = await service.update(org_id, 'admin', policy())
    assert second['status'] == 'applied', second
    assert first['operation_id'] == second['operation_id']
    assert proxy.state['team_max_budget'] == 240
    assert proxy.state['members'][user_id]['max_budget'] == 32
    writes = len(proxy.writes)
    proxy.fail_read = True
    assert await service.update(org_id, 'admin', policy()) == second
    assert len(proxy.writes) == writes


@pytest.mark.asyncio
async def test_conflicting_edits_do_not_supersede_pending_intent(managed):
    org_id, _, service, proxy = managed
    proxy.lose_next_response = True
    await service.update(org_id, 'admin', policy())
    writes = len(proxy.writes)
    with pytest.raises(BudgetControlConflict):
        await service.update(org_id, 'admin', policy(current_cycle_team_allowance=500))
    with pytest.raises(BudgetControlConflict):
        await service.update(
            org_id, 'admin', policy(idempotency_key='edit-2', expected_generation=2)
        )
    assert len(proxy.writes) == writes


@pytest.mark.asyncio
@pytest.mark.parametrize('downtime_days', [0, 100])
async def test_maintenance_recovers_one_allowance_without_inventing_past_cycles(
    managed, async_session_maker, downtime_days
):
    org_id, user_id, service, proxy = managed
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        now = settings.cycle_end_at + timedelta(days=downtime_days, seconds=1)
    proxy.state['team_spend'] = 500
    proxy.state['member_counters'][user_id]['spend'] = 50
    first = await service.maintain(org_id, now=now)
    assert first['cycle_rolled'] is True, first
    assert proxy.state['team_max_budget'] == 1200
    assert proxy.state['members'][user_id]['max_budget'] == 130
    proxy.state['team_spend'] = 550
    assert (await service.maintain(org_id, now=now))['cycle_rolled'] is False
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.cycle_start_at == now
        assert settings.cycle_start_spend == 500
        assert len((await session.scalars(select(OrgBudgetOperation))).all()) == 2


@pytest.mark.asyncio
async def test_concurrent_maintenance_runs_roll_the_cycle_only_once(
    managed, async_session_maker
):
    org_id, _, service, proxy = managed
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        now = settings.cycle_end_at + timedelta(seconds=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_observation(*args, **kwargs):
        entered.set()
        await release.wait()
        return await proxy.observe(*args, **kwargs)

    with patch.object(service, '_observe', delayed_observation):
        first = asyncio.create_task(service.maintain(org_id, now=now))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            with pytest.raises(BudgetControlConflict, match='already in progress'):
                await service.maintain(org_id, now=now)
        finally:
            release.set()
        assert (await asyncio.wait_for(first, timeout=5))['cycle_rolled'] is True
    assert (await service.maintain(org_id, now=now))['cycle_rolled'] is False


@pytest.mark.asyncio
async def test_renewal_response_loss_replays_before_new_generation(
    managed, async_session_maker
):
    org_id, _, service, proxy = managed
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        now = settings.cycle_end_at + timedelta(seconds=1)
    proxy.state['team_spend'] = 500
    proxy.lose_next_response = True
    first = await service.maintain(org_id, now=now)
    assert first['status'] == 'pending'
    proxy.state['team_spend'] = 550
    second = await service.maintain(org_id, now=now)
    assert second['status'] == 'applied', second
    assert second['operation_id'] == first['operation_id']
    assert proxy.state['team_max_budget'] == 1200


@pytest.mark.asyncio
async def test_replaced_counter_is_not_automatically_reanchored(managed):
    org_id, user_id, service, proxy = managed
    proxy.state['member_counters'][user_id]['budget_id'] = 'replacement'
    writes = len(proxy.writes)
    with pytest.raises(BudgetWriteDenied):
        await service.maintain(org_id)
    assert len(proxy.writes) == writes


@pytest.mark.asyncio
async def test_handoff_preserves_the_last_applied_caps(managed):
    org_id, _, service, proxy = managed
    writes = len(proxy.writes)
    await service.hand_off(org_id, 'admin')
    assert (await service.maintain(org_id))['status'] == 'skipped'
    assert len(proxy.writes) == writes


@pytest.mark.asyncio
@pytest.mark.parametrize('operator_blocked', [False, True])
@pytest.mark.parametrize('transition', ['rollover', 'increase', 'disable', 'stay_zero'])
async def test_zero_allowance_transition_removes_only_a_budget_owned_block(
    adoption, async_engine, async_session_maker, operator_blocked, transition
):
    org_id, user_id, proxy = await adopt_zero(
        adoption, operator_blocked=operator_blocked
    )
    assert proxy.state['team_max_budget'] == 40
    assert proxy.state['control_policy']['team']['blocked'] is True
    independent_members = deepcopy(proxy.state['control_policy']['members'])
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.cycle_allowance == 0
        cycle_end = settings.cycle_end_at
        state = await OrgBudgetService(session).get_budget_state(org_id)
        assert state['reconciliation_state'] == 'healthy'
    proxy.writes.clear()
    service = ManagedBudgetService(async_engine)
    if transition == 'rollover':
        result = await service.maintain(org_id, now=cycle_end + timedelta(seconds=1))
        assert proxy.state['team_max_budget'] == 740
        assert proxy.state['members'][user_id]['max_budget'] == 92
    else:
        result = await service.update(
            org_id,
            'admin',
            policy(
                enabled=transition != 'disable',
                current_cycle_team_allowance=0 if transition == 'stay_zero' else 200,
            ),
        )
    assert result['status'] == 'applied', result
    stays_blocked = operator_blocked or transition == 'stay_zero'
    assert proxy.state['control_policy']['team']['blocked'] is stays_blocked
    assert proxy.state['control_policy']['team']['models'] == ['model']
    assert (
        proxy.state['control_policy']['members'][user_id]['rpm_limit']
        == (independent_members[user_id]['rpm_limit'])
    )
    block_writes = [w for w in proxy.writes if 'blocked' in w['body']]
    if stays_blocked:
        assert block_writes == []
    else:
        assert block_writes == [proxy.writes[-1]]
        assert block_writes[0]['body'] == {'team_id': str(org_id), 'blocked': False}
    async with async_session_maker() as session:
        latest = await session.scalar(
            select(OrgBudgetOperation).order_by(OrgBudgetOperation.generation.desc())
        )
        assert latest.plan['team_block']['budget_owned'] is (
            transition == 'stay_zero' and not operator_blocked
        )


@pytest.mark.asyncio
async def test_zero_adoption_lost_block_response_reuses_baseline_and_operation(
    adoption, async_session_maker
):
    org_id, _, service, request, proxy = adoption
    request = request.model_copy(update={'current_team_allowance': 0})
    proxy.lose_next_response = True
    first = await service.confirm(org_id, 'admin', request)
    assert first['status'] == 'pending'
    assert proxy.writes == [
        {'path': '/team/update', 'body': {'team_id': str(org_id), 'blocked': True}}
    ]
    assert proxy.state['control_policy']['team']['blocked'] is True
    # In-flight metering may arrive after the block. Never resnapshot on retry.
    proxy.state['team_spend'] = 45
    second = await service.confirm(org_id, 'admin', request)
    assert second['status'] == 'applied', second
    assert second['operation_id'] == first['operation_id']
    assert proxy.state['team_max_budget'] == 40
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.cycle_start_spend == 40
        assert settings.cycle_allowance == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('readback_block', [False, None, 1])
async def test_zero_adoption_cannot_finish_without_verified_native_block(
    adoption, async_session_maker, readback_block
):
    org_id, _, service, request, proxy = adoption
    request = request.model_copy(update={'current_team_allowance': 0})

    async def ignore_block_write(*args, **kwargs):
        await proxy.write(*args, **kwargs)
        proxy.state['control_policy']['team']['blocked'] = readback_block
        proxy.state['team_blocked'] = readback_block

    with patch(
        'storage.lite_llm_manager.LiteLlmManager.apply_budget_write', ignore_block_write
    ):
        result = await service.confirm(org_id, 'admin', request)
    assert result['status'] == 'pending'
    assert 'block readback' in result['error']
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.control_mode == 'needs_adoption'
        assert settings.cycle_allowance is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['before_unblock', 'lost_unblock_response'])
async def test_reopening_zero_budget_retries_same_targets_after_partial_failure(
    adoption, async_engine, failure
):
    org_id, _, proxy = await adopt_zero(adoption)
    proxy.writes.clear()
    service = ManagedBudgetService(async_engine)

    async def fail_at_unblock(*args, **kwargs):
        if kwargs['body'].get('blocked') is False:
            if failure == 'before_unblock':
                raise RuntimeError('proxy unavailable before unblock')
            proxy.lose_next_response = True
        await proxy.write(*args, **kwargs)

    with patch(
        'storage.lite_llm_manager.LiteLlmManager.apply_budget_write', fail_at_unblock
    ):
        first = await service.update(org_id, 'admin', policy())
    assert first['status'] == 'pending'
    assert proxy.state['team_max_budget'] == 240
    assert proxy.state['control_policy']['team']['blocked'] is (
        failure == 'before_unblock'
    )
    proxy.state['team_spend'] = 45
    second = await service.update(org_id, 'admin', policy())
    assert second['status'] == 'applied', second
    assert second['operation_id'] == first['operation_id']
    assert proxy.state['team_max_budget'] == 240
    assert proxy.state['control_policy']['team']['blocked'] is False
    assert proxy.writes[-1]['body'] == {'team_id': str(org_id), 'blocked': False}


@pytest.mark.asyncio
async def test_missing_zero_budget_block_is_not_healthy_or_silently_repaired(
    adoption, async_engine, async_session_maker
):
    org_id, _, proxy = await adopt_zero(adoption)
    proxy.state['team_blocked'] = False
    proxy.state['control_policy']['team']['blocked'] = False
    proxy.writes.clear()
    async with async_session_maker() as session:
        state = await OrgBudgetService(session).get_budget_state(org_id)
        assert state['budget_policy_matches'] is False
        assert 'zero_allowance_team_block' in state['reconciliation_error']
    with pytest.raises(BudgetWriteDenied, match='Team block readback'):
        await ManagedBudgetService(async_engine).maintain(org_id)
    assert proxy.writes == []


@pytest.mark.asyncio
async def test_handoff_and_readoption_do_not_inherit_authority_to_unblock(
    adoption, async_engine
):
    org_id, _, proxy = await adopt_zero(adoption)
    service = ManagedBudgetService(async_engine)
    writes = deepcopy(proxy.writes)
    await service.hand_off(org_id, 'admin')
    assert proxy.writes == writes
    request = adoption[3].model_copy(
        update={
            'idempotency_key': 'readopt',
            'preview_fingerprint': (await service.preview(org_id))['fingerprint'],
        }
    )
    result = await service.confirm(org_id, 'admin', request)
    assert result['status'] == 'applied', result
    assert proxy.state['control_policy']['team']['blocked'] is True
    assert all('blocked' not in w['body'] for w in proxy.writes[len(writes) :])


@pytest.mark.asyncio
async def test_existing_maintenance_entrypoint_retries_pending_adoption(
    adoption, async_session_maker
):
    org_id, _, service, request, proxy = adoption
    proxy.lose_next_response = True
    assert (await service.confirm(org_id, 'admin', request))['status'] == 'pending'
    async with async_session_maker() as session:
        result = await OrgBudgetService(session).run_budget_maintenance(org_id)
    assert result['status'] == 'applied', result
    assert result['control_mode'] == 'managed'


@pytest.mark.asyncio
async def test_failed_operation_is_visible_even_when_old_caps_still_match(
    managed, async_session_maker
):
    org_id, _, service, proxy = managed

    async def fail_before_write(*args, **kwargs):
        raise RuntimeError('proxy unavailable')

    with patch(
        'storage.lite_llm_manager.LiteLlmManager.apply_budget_write', fail_before_write
    ):
        result = await service.update(org_id, 'admin', policy())
    assert result['status'] == 'pending'
    async with async_session_maker() as session:
        state = await OrgBudgetService(session).get_budget_state(org_id)
        assert state['budget_policy_matches'] is True
        assert state['reconciliation_state'] == 'failed'
        assert state['pending_operation_id'] == result['operation_id']


@pytest.mark.asyncio
async def test_legacy_override_api_cannot_mutate_adopted_settings(
    managed, async_session_maker
):
    from fastapi import HTTPException

    org_id, user_id, _, proxy = managed
    writes = len(proxy.writes)
    async with async_session_maker() as session:
        with pytest.raises(HTTPException) as exc:
            await OrgBudgetService(session).upsert_user_override(
                org_id, user_id, 50, False
            )
        assert exc.value.status_code == 409
        assert (
            await OrgBudgetService(session).store.get_override(org_id, user_id) is None
        )
    assert len(proxy.writes) == writes


@pytest.mark.asyncio
async def test_stale_callers_session_does_not_reanchor_after_a_completed_roll(
    managed, async_session_maker
):
    org_id, _, controller, proxy = managed
    async with async_session_maker() as stale_session:
        stale_settings = await stale_session.scalar(select(OrgBudgetSettings))
        now = stale_settings.cycle_end_at + timedelta(seconds=1)
        proxy.state['team_spend'] = 100
        first = await controller.maintain(org_id, now=now)
        assert first['cycle_rolled'] is True
        proxy.state['team_spend'] = 110
        caller = OrgBudgetService(stale_session)

        async def maintain_at_same_time(org_id):
            return await controller.maintain(org_id, now=now)

        with patch.object(caller, '_controller') as get_controller:
            from unittest.mock import AsyncMock

            get_controller.return_value.maintain = AsyncMock(
                side_effect=maintain_at_same_time
            )
            second = await caller.run_budget_maintenance(org_id)
        assert second['cycle_rolled'] is False
        assert stale_settings.cycle_start_spend == 100


@pytest.mark.asyncio
async def test_disabled_policy_and_reenable_preserve_original_baselines(managed):
    org_id, user_id, service, proxy = managed
    assert (await service.update(org_id, 'admin', policy(enabled=False)))[
        'status'
    ] == 'applied'
    assert proxy.state['team_max_budget'] is None
    proxy.state['team_spend'] = 100
    proxy.state['member_counters'][user_id]['spend'] = 20
    assert (
        await service.update(
            org_id, 'admin', policy(idempotency_key='enable-2', expected_generation=2)
        )
    )['status'] == 'applied'
    assert proxy.state['team_max_budget'] == 240
    assert proxy.state['members'][user_id]['max_budget'] == 32
