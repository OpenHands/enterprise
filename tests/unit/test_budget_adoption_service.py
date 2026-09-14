"""Adoption state transitions with PostgreSQL and a fault-injectable proxy."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import select

from server.services.budget_adoption_plan import BudgetAdoptionRequest
from server.services.budget_adoption_service import BudgetAdoptionService
from server.services.org_budget_service import OrgBudgetService
from storage.budget_control import BudgetControlConflict, current_budget_control
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.role import Role


class Proxy:
    def __init__(self, user_id):
        self.writes = []
        self.observations = 0
        self.lose_next_response = False
        self.fail_read = False
        self.state = {
            'team_spend': 40.0,
            'team_max_budget': 500.0,
            'team_reset_known': True,
            'team_budget_duration': None,
            'team_budget_reset_at': None,
            'default_member_budget_id': None,
            'default_member_budget': None,
            'members': {
                user_id: {
                    'spend': 12.0,
                    'max_budget': 25.0,
                    'uses_shared_budget': False,
                }
            },
            'member_counters': {
                user_id: {
                    'spend': 12.0,
                    'source': 'membership',
                    'budget_id': 'private',
                    'effective_budget_id': 'private',
                    'budget_source': 'private_member',
                    'budget_duration': None,
                    'budget_reset_at': None,
                    'reset_known': True,
                }
            },
            'control_policy': {
                'team': {
                    'max_budget': 500.0,
                    'models': ['model'],
                    'blocked': False,
                    'budget_duration': None,
                    'budget_reset_at': None,
                },
                'members': {user_id: {'rpm_limit': 9}},
                'keys': [],
                'default_member': {},
            },
        }

    async def observe(self, org_id, **kwargs):
        self.observations += 1
        if self.fail_read:
            raise RuntimeError('proxy unavailable')
        return deepcopy(self.state)

    async def write(self, org_id, operation_id, path, body):
        await current_budget_control(org_id).authorize_write(operation_id, path, body)
        self.writes.append({'path': path, 'body': deepcopy(body)})
        if path == '/team/update':
            self.state['team_max_budget'] = body['max_budget']
            self.state['control_policy']['team']['max_budget'] = body['max_budget']
        else:
            member = self.state['members'][body['user_id']]
            member['max_budget'] = body['max_budget_in_team']
            member['uses_shared_budget'] = body['max_budget_in_team'] is None
        if self.lose_next_response:
            self.lose_next_response = False
            raise RuntimeError('response lost after applying write')


@pytest.fixture
async def adoption(create_org, create_user, async_session_maker, async_engine):
    org = create_org()
    user = create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        role = Role(name='budget-admin', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=org.id,
                user_id=user.id,
                role_id=role.id,
                _llm_api_key='test-only',
            )
        )
        session.add(
            OrgBudgetSettings(
                org_id=org.id,
                enabled=True,
                control_mode='needs_adoption',
                monthly_limit=1,
                default_user_monthly_limit=1,
            )
        )
        await session.commit()
    proxy = Proxy(str(user.id))
    service = BudgetAdoptionService(async_engine)
    with (
        patch(
            'storage.lite_llm_manager.LiteLlmManager.get_team_members_financial_data',
            proxy.observe,
        ),
        patch(
            'storage.lite_llm_manager.LiteLlmManager.apply_budget_write', proxy.write
        ),
    ):
        preview = await service.preview(org.id)
        request = BudgetAdoptionRequest(
            preview_fingerprint=preview['fingerprint'],
            idempotency_key='confirm-1',
            current_team_allowance=100,
            current_default_member_allowance=10,
            future_monthly_limit=700,
            future_default_member_limit=80,
            reset_day=1,
            replace_native_reset_schedules=False,
        )
        yield org.id, str(user.id), service, request, proxy


@pytest.mark.asyncio
async def test_adoption_commits_exact_current_and_future_policy(
    adoption, async_session_maker
):
    org_id, user_id, service, request, proxy = adoption
    assert not proxy.writes
    result = await service.confirm(org_id, 'admin', request)
    assert result['status'] == 'applied', result
    assert result['control_mode'] == 'managed'
    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )
        assert settings.monthly_limit == 700
        assert settings.cycle_allowance == 100
        assert settings.default_user_monthly_limit == 80
        assert settings.cycle_default_user_allowance == 10
        assert settings.cycle_start_spend == 40
        assert settings.user_cycle_start_spend == {user_id: 12}
        baseline = await session.scalar(select(OrgBudgetCycleBaseline))
        assert baseline.source == 'adoption'
        assert baseline.baseline_spend == 12
    assert proxy.state['team_max_budget'] == 140
    assert proxy.state['members'][user_id]['max_budget'] == 22


@pytest.mark.asyncio
async def test_existing_budget_read_uses_current_allowance_not_future_limit(
    adoption, async_session_maker
):
    org_id, user_id, service, request, _ = adoption
    await service.confirm(org_id, 'admin', request)
    async with async_session_maker() as session:
        state = await OrgBudgetService(session).get_budget_state(org_id)
        assert state['reconciliation_state'] == 'healthy'
        assert state['desired_team_max_budget'] == 140
        assert state['budget_policy_matches'] is True
        assert state['settings'].monthly_limit == 700
        assert state['settings'].cycle_allowance == 100
        assert state['users'][0]['user_id'] == user_id
        assert state['users'][0]['effective_monthly_limit'] == 10


@pytest.mark.asyncio
async def test_lost_response_then_spend_growth_reuses_first_baseline(
    adoption, async_session_maker
):
    org_id, user_id, service, request, proxy = adoption
    proxy.lose_next_response = True
    failed = await service.confirm(org_id, 'admin', request)
    assert failed['status'] == 'pending'
    assert failed['control_mode'] == 'needs_adoption'
    proxy.state['team_spend'] = 90
    proxy.state['member_counters'][user_id]['spend'] = 20
    recovered = await service.confirm(org_id, 'admin', request)
    assert recovered['operation_id'] == failed['operation_id']
    assert recovered['status'] == 'applied', recovered
    assert proxy.state['team_max_budget'] == 140
    assert proxy.state['members'][user_id]['max_budget'] == 22
    async with async_session_maker() as session:
        operations = (await session.scalars(select(OrgBudgetOperation))).all()
        assert len(operations) == 1
        assert operations[0].plan['team_baseline'] == 40


@pytest.mark.asyncio
async def test_successful_retry_is_noop_even_if_proxy_is_now_unavailable(adoption):
    org_id, _, service, request, proxy = adoption
    first = await service.confirm(org_id, 'admin', request)
    reads, writes = proxy.observations, len(proxy.writes)
    proxy.fail_read = True
    retried = await service.confirm(org_id, 'admin', request)
    assert first == retried
    assert (proxy.observations, len(proxy.writes)) == (reads, writes)


@pytest.mark.asyncio
async def test_counter_reset_blocks_replay_without_granting_new_allowance(adoption):
    org_id, user_id, service, request, proxy = adoption
    proxy.lose_next_response = True
    await service.confirm(org_id, 'admin', request)
    writes = len(proxy.writes)
    proxy.state['member_counters'][user_id]['spend'] = 0
    retry = await service.retry(org_id)
    assert retry['status'] == 'pending'
    assert 'counter reset' in retry['error']
    assert len(proxy.writes) == writes


@pytest.mark.asyncio
async def test_handoff_stops_retries_and_does_not_restore_old_caps(adoption):
    org_id, _, service, request, proxy = adoption
    proxy.lose_next_response = True
    await service.confirm(org_id, 'admin', request)
    writes = len(proxy.writes)
    before = deepcopy(proxy.state)
    assert (await service.hand_off(org_id, 'admin'))['control_mode'] == 'external'
    assert await service.retry(org_id) is None
    with pytest.raises(BudgetControlConflict, match='handed off'):
        await service.confirm(org_id, 'admin', request)
    assert len(proxy.writes) == writes
    assert proxy.state == before


@pytest.mark.asyncio
async def test_external_cap_edit_blocks_pending_replay(adoption):
    org_id, _, service, request, proxy = adoption
    proxy.lose_next_response = True
    await service.confirm(org_id, 'admin', request)
    writes = len(proxy.writes)
    proxy.state['team_max_budget'] = 120
    proxy.state['control_policy']['team']['max_budget'] = 120
    retry = await service.retry(org_id)
    assert retry['status'] == 'pending'
    assert 'outside the pending operation' in retry['error']
    assert len(proxy.writes) == writes
