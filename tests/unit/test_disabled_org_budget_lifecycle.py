"""Individual limits keep their lifecycle when the organization cap is off."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time

from run_budget_maintenance import _eligible_budget_org_ids
from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import OrgBudgetService
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from tests.unit.test_org_budget_service import _financial_data, _snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'action,expected_cap',
    [
        ('default', 28),
        ('clear_default', None),
        ('override', 28),
        ('disable_override', None),
        ('delete', 38),
        ('rollover', 42),
        ('repair', 38),
        ('resave', 38),
        ('recover', 38),
    ],
)
async def test_retained_limits_are_applied_and_renewed(
    action, expected_cap, async_session_maker, session_maker, create_org, create_user
):
    org = create_org()
    user = create_user(current_org_id=org.id)
    native_cap = 18.0 if action in {'delete', 'repair'} else 38.0
    data = _financial_data(
        team_spend=12.0, members={str(user.id): (12.0, native_cap, False)}
    )
    events = []

    async def block(_org, blocked):
        events.append(('block', blocked))

    async def update_member(_user, _org, max_budget, clear_budget=False):
        events.append(('member', max_budget))
        data['members'][str(user.id)].update(
            max_budget=max_budget, uses_shared_budget=clear_budget
        )

    with (
        freeze_time('2026-09-24 12:00:00'),
        patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(side_effect=lambda *_args, **_kwargs: data),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.set_team_blocked',
            AsyncMock(side_effect=block),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_team', AsyncMock()
        ) as team_write,
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
            AsyncMock(side_effect=update_member),
        ) as member_write,
    ):
        async with async_session_maker() as session:
            role = Role(name='owner', rank=0)
            session.add(role)
            await session.flush()
            settings = OrgBudgetSettings(
                org_id=org.id,
                enabled=False,
                monthly_limit=100.0,
                default_user_monthly_limit=30.0,
                reset_day=1,
                cycle_start_at=datetime(2026, 9, 1, tzinfo=UTC),
                cycle_start_spend=8.0,
                user_cycle_start_spend={str(user.id): 8.0},
                litellm_known_member_ids=[str(user.id)],
                litellm_last_sync_status='error' if action == 'recover' else 'success',
            )
            session.add_all(
                [
                    OrgMember(
                        org_id=org.id,
                        user_id=user.id,
                        role_id=role.id,
                        status='active',
                        llm_api_key='test-only',
                    ),
                    settings,
                ]
            )
            if action == 'delete':
                session.add(
                    OrgUserBudgetOverride(
                        org_id=org.id,
                        user_id=user.id,
                        monthly_limit=10,
                        is_disabled=False,
                    )
                )
            await session.commit()
            service = OrgBudgetService(session)
            if action in {'default', 'clear_default'}:
                await service.update_budget_settings(
                    org.id,
                    OrgBudgetSettingsUpdate(
                        default_user_monthly_limit=20 if action == 'default' else None
                    ),
                )
            elif action in {'override', 'disable_override'}:
                await service.upsert_user_override(
                    org.id,
                    user.id,
                    monthly_limit=20,
                    is_disabled=action == 'disable_override',
                )
            elif action == 'delete':
                await service.delete_user_override(org.id, user.id)
            elif action == 'rollover':
                with freeze_time('2026-10-02 12:00:00'):
                    result = await service.run_budget_maintenance(org.id)
                    assert result['cycle_rolled'] is True
                    assert settings.user_cycle_start_spend[str(user.id)] == 12
            elif action == 'repair':
                await service.run_budget_maintenance(org.id)
            else:
                await service.update_budget_settings(
                    org.id, OrgBudgetSettingsUpdate(enabled=False)
                )
            state = await service.get_budget_state(org.id)
            assert state['budget_policy_matches'] is True
            assert state['reconciliation_state'] == 'inactive'
            assert settings.litellm_last_sync_status == 'success'
            assert data['members'][str(user.id)]['max_budget'] == expected_cap
            if action == 'resave':
                assert events == []
                team_write.assert_not_awaited()
                member_write.assert_not_awaited()
            else:
                assert events[0] == ('block', True)
                assert events[-1] == ('block', False)
                member_write.assert_awaited_once()
            await session.commit()
        with session_maker() as session:
            assert str(org.id) in _eligible_budget_org_ids(session)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'baseline,cap,matches', [(8, 38, True), (8, 28, False), (None, 38, False)]
)
async def test_disabled_policy_comparisons_agree(
    baseline, cap, matches, async_session_maker, create_org, create_user
):
    org = create_org()
    user = create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        role = Role(name='owner', rank=0)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=org.id, user_id=user.id, role_id=role.id, llm_api_key='test-only'
            )
        )
        settings = OrgBudgetSettings(
            org_id=org.id,
            enabled=False,
            default_user_monthly_limit=30,
            user_cycle_start_spend={} if baseline is None else {str(user.id): baseline},
            litellm_last_sync_status='success',
        )
        session.add(settings)
        await session.flush()
        assert (
            await OrgBudgetService(session)._budget_policy_matches_snapshot(
                org.id,
                settings,
                [],
                _snapshot(members={str(user.id): (12, cap, False)}),
            )
            is matches
        )


@pytest.mark.parametrize(
    'policy',
    ['default', 'override', 'disabled_override', 'pending', 'error', 'success'],
)
def test_scheduler_includes_retained_or_unconfirmed_policies(
    policy, create_org, create_user, session_maker
):
    org = create_org()
    user = create_user(current_org_id=org.id)
    with session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=org.id,
                enabled=False,
                default_user_monthly_limit=0 if policy == 'default' else None,
                litellm_last_sync_status=policy
                if policy in {'pending', 'error', 'success'}
                else None,
            )
        )
        if policy in {'override', 'disabled_override'}:
            session.add(
                OrgUserBudgetOverride(
                    org_id=org.id,
                    user_id=user.id,
                    monthly_limit=20,
                    is_disabled=policy == 'disabled_override',
                )
            )
        session.commit()
        assert str(org.id) in _eligible_budget_org_ids(session)
