from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from freezegun import freeze_time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from run_budget_maintenance import _eligible_budget_org_ids
from server.constants import ORG_SETTINGS_VERSION
from server.routes.org_models import (
    OrgBudgetSettingsUpdate,
    OrgBudgetThresholdUpdate,
)
from server.services.org_budget_service import (
    _UNIQUE_VIOLATION,
    DEFAULT_THRESHOLDS,
    BudgetFinancialSnapshotResult,
    LiteLlmFinancialSnapshot,
    LiteLlmMemberFinancialSnapshot,
    OrgBudgetService,
    _budget_policy_comparison,
    _current_cycle_start,
    _next_cycle_start,
)
from storage.org import Org
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from storage.user import User


def _snapshot(
    *,
    team_spend: float = 0.0,
    team_max_budget: float | None = None,
    members: dict[str, tuple[float, float | None, bool]] | None = None,
) -> LiteLlmFinancialSnapshot:
    return LiteLlmFinancialSnapshot(
        team_spend=team_spend,
        team_max_budget=team_max_budget,
        members={
            user_id: LiteLlmMemberFinancialSnapshot(
                spend=spend,
                max_budget=max_budget,
                uses_shared_budget=uses_shared_budget,
            )
            for user_id, (spend, max_budget, uses_shared_budget) in (
                members or {}
            ).items()
        },
        observed_at=datetime.now(UTC),
    )


def _financial_data(
    *,
    team_spend: float = 0.0,
    team_max_budget: float | None = None,
    members: dict[str, tuple[float, float | None, bool]] | None = None,
) -> dict:
    return {
        'team_spend': team_spend,
        'team_max_budget': team_max_budget,
        'members': {
            user_id: {
                'spend': spend,
                'max_budget': max_budget,
                'uses_shared_budget': uses_shared_budget,
            }
            for user_id, (spend, max_budget, uses_shared_budget) in (
                members or {}
            ).items()
        },
    }


def test_budget_policy_comparison_reports_verified_healthy_state():
    user_id = str(uuid4())
    applied_at = datetime.now(UTC)
    settings = OrgBudgetSettings(
        org_id=uuid4(),
        enabled=True,
        monthly_limit=100.0,
        default_user_monthly_limit=30.0,
        cycle_start_spend=20.0,
        user_cycle_start_spend={user_id: 8.0},
        litellm_last_sync_status='success',
        litellm_last_sync_at=applied_at,
    )
    snapshot = _snapshot(
        team_max_budget=120.0,
        members={user_id: (8.0, 38.0, False)},
    )

    result = _budget_policy_comparison(
        settings,
        [],
        {user_id},
        BudgetFinancialSnapshotResult(snapshot=snapshot, status='live'),
    )

    assert result['reconciliation_state'] == 'healthy'
    assert result['budget_policy_matches'] is True
    assert result['desired_team_max_budget'] == 120.0
    assert result['applied_team_max_budget'] == 120.0
    assert result['applied_at'] == applied_at


@pytest.mark.asyncio
async def test_get_reconciliation_state_reports_sync_error_as_degraded():
    settings = OrgBudgetSettings(
        org_id=uuid4(),
        enabled=True,
        monthly_limit=100.0,
        litellm_last_sync_status='error',
    )
    store = MagicMock()
    store.db_session = None
    store.get_settings = AsyncMock(return_value=settings)
    # The settings loader now hydrates cycle baselines from the table; this org
    # has no rows, so the JSON map is left as it is.
    store.get_cycle_baselines = AsyncMock(return_value={})
    service = OrgBudgetService(store=store)

    assert await service.get_reconciliation_state(settings.org_id) == 'degraded'


def test_budget_policy_comparison_reports_live_drift_as_degraded():
    user_id = str(uuid4())
    settings = OrgBudgetSettings(
        org_id=uuid4(),
        enabled=True,
        monthly_limit=100.0,
        default_user_monthly_limit=30.0,
        cycle_start_spend=20.0,
        user_cycle_start_spend={user_id: 8.0},
        litellm_last_sync_status='success',
    )
    snapshot = _snapshot(
        team_max_budget=2.05,
        members={user_id: (8.0, 2.05, False)},
    )

    result = _budget_policy_comparison(
        settings,
        [],
        {user_id},
        BudgetFinancialSnapshotResult(snapshot=snapshot, status='live'),
    )

    assert result['reconciliation_state'] == 'degraded'
    assert result['budget_policy_matches'] is False
    assert result['reconciliation_error'].startswith('team_budget_mismatch')
    assert result['applied_at'] is None


def test_budget_policy_comparison_reports_unreadable_failed_state():
    settings = OrgBudgetSettings(
        org_id=uuid4(),
        enabled=True,
        monthly_limit=100.0,
        cycle_start_spend=20.0,
        litellm_last_sync_status='error',
        litellm_last_sync_error='verification_fetch_failed: timeout',
    )

    result = _budget_policy_comparison(
        settings,
        [],
        set(),
        BudgetFinancialSnapshotResult(
            snapshot=None,
            status='unavailable',
            error='timeout',
        ),
    )

    assert result['reconciliation_state'] == 'failed'
    assert result['budget_policy_matches'] is None
    assert result['applied_team_max_budget'] is None
    assert result['reconciliation_error'] == 'verification_fetch_failed: timeout'


def test_budget_policy_comparison_reports_beta_missing_baseline_shape():
    user_id = str(uuid4())
    settings = OrgBudgetSettings(
        org_id=uuid4(),
        enabled=True,
        monthly_limit=1000.0,
        default_user_monthly_limit=300.0,
        cycle_start_spend=2.0,
        user_cycle_start_spend={},
        litellm_known_member_ids=[user_id],
        litellm_last_sync_status='error',
        litellm_last_sync_error=f'member_cycle_baseline_missing: {user_id}',
    )
    snapshot = _snapshot(
        team_spend=2.29,
        team_max_budget=1002.0,
        members={user_id: (2.29, 1002.0, True)},
    )

    result = _budget_policy_comparison(
        settings,
        [],
        {user_id},
        BudgetFinancialSnapshotResult(snapshot=snapshot, status='live'),
    )

    assert result['desired_team_max_budget'] == 1002.0
    assert result['applied_team_max_budget'] == 1002.0
    assert result['budget_policy_matches'] is False
    assert result['reconciliation_state'] == 'degraded'
    assert 'member_cycle_baseline_missing' in result['reconciliation_error']


@pytest.fixture
async def budget_org(async_session_maker):
    org_id = uuid4()
    async with async_session_maker() as session:
        org = Org(
            id=org_id,
            name=f'test-org-{org_id}',
            org_version=ORG_SETTINGS_VERSION,
            enable_proactive_conversation_starters=True,
        )
        session.add(org)
        await session.commit()
    return org


@pytest.fixture
async def personal_org(async_session_maker):
    user_id = uuid4()
    async with async_session_maker() as session:
        org = Org(
            id=user_id,
            name=f'user_{user_id}_org',
            org_version=ORG_SETTINGS_VERSION,
            enable_proactive_conversation_starters=True,
        )
        user = User(id=user_id, current_org_id=user_id)
        session.add_all([org, user])
        await session.commit()
    return org


def test_budget_maintenance_scheduler_excludes_personal_and_disabled_orgs(
    session_maker,
):
    personal_id = uuid4()
    disabled_team_id = uuid4()
    team_id = uuid4()
    now = datetime.now(UTC)

    with session_maker() as session:
        session.add_all(
            [
                Org(
                    id=personal_id,
                    name=f'user_{personal_id}_org',
                    org_version=ORG_SETTINGS_VERSION,
                    enable_proactive_conversation_starters=True,
                ),
                Org(
                    id=disabled_team_id,
                    name=f'test-org-{disabled_team_id}',
                    org_version=ORG_SETTINGS_VERSION,
                    enable_proactive_conversation_starters=True,
                ),
                Org(
                    id=team_id,
                    name=f'test-org-{team_id}',
                    org_version=ORG_SETTINGS_VERSION,
                    enable_proactive_conversation_starters=True,
                ),
                User(id=personal_id, current_org_id=personal_id),
            ]
        )
        session.flush()
        session.add_all(
            [
                OrgBudgetSettings(
                    org_id=personal_id,
                    enabled=False,
                    reset_day=1,
                    cycle_start_at=now,
                    cycle_start_spend=0.0,
                ),
                OrgBudgetSettings(
                    org_id=disabled_team_id,
                    enabled=False,
                    reset_day=1,
                    cycle_start_at=now,
                    cycle_start_spend=0.0,
                ),
                OrgBudgetSettings(
                    org_id=team_id,
                    enabled=True,
                    monthly_limit=100.0,
                    reset_day=1,
                    cycle_start_at=now,
                    cycle_start_spend=0.0,
                ),
            ]
        )
        session.commit()

        org_ids = _eligible_budget_org_ids(session)

    assert org_ids == [str(team_id)]


def test_cleanup_migration_removes_only_personal_org_settings(session_maker):
    migration = import_module(
        'migrations.versions.148_remove_personal_org_budget_settings'
    )
    personal_id = uuid4()
    team_id = uuid4()
    now = datetime.now(UTC)

    with session_maker() as session:
        session.add_all(
            [
                Org(
                    id=personal_id,
                    name=f'user_{personal_id}_org',
                    org_version=ORG_SETTINGS_VERSION,
                    enable_proactive_conversation_starters=True,
                ),
                Org(
                    id=team_id,
                    name=f'test-org-{team_id}',
                    org_version=ORG_SETTINGS_VERSION,
                    enable_proactive_conversation_starters=True,
                ),
                User(id=personal_id, current_org_id=personal_id),
            ]
        )
        session.flush()
        session.add_all(
            [
                OrgBudgetSettings(
                    org_id=personal_id,
                    reset_day=1,
                    cycle_start_at=now,
                    cycle_start_spend=0.0,
                ),
                OrgBudgetSettings(
                    org_id=team_id,
                    reset_day=1,
                    cycle_start_at=now,
                    cycle_start_spend=0.0,
                ),
            ]
        )
        session.commit()

        with patch.object(migration.op, 'execute', side_effect=session.execute):
            migration.upgrade()
        session.commit()

        remaining_org_ids = {
            row.org_id for row in session.query(OrgBudgetSettings.org_id)
        }

    assert remaining_org_ids == {team_id}


@pytest.mark.asyncio
async def test_budget_operations_reject_personal_org_without_creating_settings(
    async_session_maker, personal_org
):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)

        with pytest.raises(HTTPException) as read_error:
            await service.get_budget_state(personal_org.id)
        with pytest.raises(HTTPException) as update_error:
            await service.update_budget_settings(
                personal_org.id,
                OrgBudgetSettingsUpdate(enabled=True, monthly_limit=100),
            )

        result = await session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == personal_org.id)
        )

    assert read_error.value.status_code == status.HTTP_400_BAD_REQUEST
    assert update_error.value.status_code == status.HTTP_400_BAD_REQUEST
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_update_budget_settings_marks_explicit_disable_for_cap_clear(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            monthly_limit=100.0,
            reset_day=1,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=0.0,
        )
        session.add(settings)
        await session.commit()
        service = OrgBudgetService(session)

        with (
            patch.object(service, '_get_thresholds', AsyncMock(return_value=[])),
            patch.object(service, '_get_overrides', AsyncMock(return_value=[])),
            patch.object(
                service, '_sync_litellm_budgets', AsyncMock(return_value=None)
            ) as sync_mock,
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=None, status='unavailable'
                    )
                ),
            ),
            patch.object(
                service, '_build_user_budget_rows', AsyncMock(return_value=([], 0))
            ),
        ):
            await service.update_budget_settings(
                budget_org.id,
                OrgBudgetSettingsUpdate(enabled=False),
            )

    sync_mock.assert_awaited_once_with(
        budget_org.id,
        settings,
        [],
        clear_disabled=True,
        snapshot=None,
    )


@pytest.mark.asyncio
async def test_run_budget_maintenance_skips_legacy_personal_org_settings(
    async_session_maker, personal_org
):
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=personal_org.id,
                enabled=False,
                reset_day=1,
                monthly_limit=None,
                default_user_monthly_limit=None,
                cycle_start_at=datetime.now(UTC),
                cycle_start_spend=0.0,
            )
        )
        await session.commit()
        service = OrgBudgetService(session)

        with patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock:
            result = await service.run_budget_maintenance(personal_org.id)

    assert result['skipped'] == 'personal_org'
    sync_mock.assert_not_awaited()


# These tests assert cycle-roll semantics that depend on today's date, so we pin
# "now" with freeze_time. A roll settles the anchor at the current period, so the
# assertions compare against `_current_cycle_start(now)`; without a frozen clock they
# would read datetime.now() and only hold on some days of the month. The dates below
# spread across month/year boundaries, a leap day, and days early and late in the
# month, so a stale anchor lands one *or* two periods back and a single roll must
# still reach the current period in every case.
_ROLL_SEMANTICS_DATES = [
    '2026-03-03',  # early in the month: anchor lands two periods back
    '2026-03-14',  # mid-month: anchor lands exactly one period back
    '2026-02-05',  # short month
    '2028-02-29',  # leap day
    '2026-01-05',  # just after a year boundary
    '2026-12-31',  # just before a year boundary
]


@pytest.mark.asyncio
@pytest.mark.parametrize('fake_today', _ROLL_SEMANTICS_DATES)
async def test_roll_cycle_if_needed_updates_cycle(
    async_session_maker, budget_org, fake_today
):
    with freeze_time(fake_today):
        async with async_session_maker() as session:
            now = datetime.now(UTC)
            reset_day = 1
            past_cycle_start = _current_cycle_start(now - timedelta(days=40), reset_day)
            settings = OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=reset_day,
                monthly_limit=250.0,
                default_user_monthly_limit=None,
                slack_channel=None,
                slack_team_id=None,
                cycle_start_at=past_cycle_start,
                cycle_start_spend=10.0,
                user_cycle_start_spend={'existing-user': 4.0},
            )
            threshold = OrgBudgetThreshold(
                org_id=budget_org.id,
                percentage=80,
                email_enabled=True,
                slack_enabled=False,
                last_triggered_at=now,
                last_triggered_cycle_start=past_cycle_start,
            )
            session.add(settings)
            session.add(threshold)
            await session.commit()

            service = OrgBudgetService(session)
            overrides: list[OrgUserBudgetOverride] = []
            snapshot = _snapshot(
                team_spend=42.5,
                members={'member': (8.0, None, True)},
            )

            with patch.object(
                service, '_sync_litellm_budgets', AsyncMock()
            ) as sync_mock:
                rolled = await service._roll_cycle_if_needed(
                    settings, [threshold], overrides, snapshot
                )

            assert rolled is True
            # A roll settles the anchor at the period containing "now" in a single
            # step, so an org that is one or several periods behind lands on the
            # current period and later runs are no-ops.
            assert settings.cycle_start_at.replace(tzinfo=UTC) == _current_cycle_start(
                now, reset_day
            )
            assert settings.cycle_start_spend == 42.5
            assert settings.user_cycle_start_spend == {'member': 8.0}
            assert threshold.last_triggered_at is None
            assert threshold.last_triggered_cycle_start is None
            sync_mock.assert_awaited_once_with(
                settings.org_id, settings, overrides, snapshot=snapshot
            )


@pytest.mark.asyncio
@pytest.mark.parametrize('fake_today', _ROLL_SEMANTICS_DATES)
async def test_roll_cycle_catches_up_to_the_current_period_in_one_run(
    async_session_maker, budget_org, fake_today
):
    # An org that has been behind for several periods (paused CronJob, outage) must
    # catch up to the current period in a single roll. Advancing only one period per
    # run would leave the anchor behind, so every later run would roll again and
    # re-anchor the baseline to today's cumulative spend -- forgiving recent spend and
    # renewing the cap each run. A single roll therefore lands on
    # _current_cycle_start(now) no matter how far behind the stale anchor is.
    with freeze_time(fake_today):
        async with async_session_maker() as session:
            now = datetime.now(UTC)
            reset_day = 1
            stale_cycle_start = _current_cycle_start(
                now - timedelta(days=200), reset_day
            )
            settings = OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=reset_day,
                monthly_limit=250.0,
                cycle_start_at=stale_cycle_start,
                cycle_start_spend=0.0,
            )
            session.add(settings)
            await session.commit()

            service = OrgBudgetService(session)
            snapshot = _snapshot(team_spend=42.5)

            with patch.object(service, '_sync_litellm_budgets', AsyncMock()):
                rolled = await service._roll_cycle_if_needed(settings, [], [], snapshot)

            assert rolled is True
            assert settings.cycle_start_at.replace(tzinfo=UTC) == _current_cycle_start(
                now, reset_day
            )


@pytest.mark.asyncio
async def test_roll_cycle_if_needed_noop(async_session_maker, budget_org):
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        reset_day = 1
        current_cycle_start = _current_cycle_start(now, reset_day)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=reset_day,
            monthly_limit=250.0,
            default_user_monthly_limit=None,
            slack_channel=None,
            slack_team_id=None,
            cycle_start_at=current_cycle_start,
            cycle_start_spend=10.0,
        )
        threshold = OrgBudgetThreshold(
            org_id=budget_org.id,
            percentage=80,
            email_enabled=True,
            slack_enabled=False,
            last_triggered_at=now,
            last_triggered_cycle_start=current_cycle_start,
        )
        session.add(settings)
        session.add(threshold)
        await session.commit()

        service = OrgBudgetService(session)
        snapshot = _snapshot(team_spend=42.5)
        with patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock:
            rolled = await service._roll_cycle_if_needed(
                settings, [threshold], [], snapshot
            )

        assert rolled is False
        assert settings.cycle_start_at == current_cycle_start
        assert threshold.last_triggered_at == now
        sync_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_budget_maintenance_syncs_when_cycle_not_rolled(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        snapshot = _snapshot(team_spend=0.0)
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=snapshot, status='live'
                    )
                ),
            ),
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            result = await service.run_budget_maintenance(budget_org.id)

    assert result['cycle_rolled'] is False
    sync_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_budget_maintenance_uses_cycle_roll_sync(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        reset_day = 1
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=reset_day,
            monthly_limit=250.0,
            default_user_monthly_limit=None,
            slack_channel=None,
            slack_team_id=None,
            cycle_start_at=_current_cycle_start(
                datetime.now(UTC) - timedelta(days=40), reset_day
            ),
            cycle_start_spend=10.0,
        )
        session.add(settings)
        await session.commit()

        service = OrgBudgetService(session)
        snapshot = _snapshot(team_spend=42.5)
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=snapshot, status='live'
                    )
                ),
            ),
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            result = await service.run_budget_maintenance(budget_org.id)

    assert result['cycle_rolled'] is True
    sync_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cycle_roll_repairs_missing_litellm_member(
    async_session_maker, budget_org
):
    user_id = uuid4()
    old_cycle_start = _current_cycle_start(datetime.now(UTC) - timedelta(days=40), 1)
    initial = _financial_data(team_spend=42.0, team_max_budget=110.0)
    repaired = _financial_data(
        team_spend=42.0,
        team_max_budget=110.0,
        members={str(user_id): (0.0, 30.0, False)},
    )

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=old_cycle_start,
            cycle_start_spend=10.0,
            litellm_known_member_ids=[str(user_id)],
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(
                    id=user_id,
                    current_org_id=budget_org.id,
                    email='repair@example.com',
                ),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[initial, repaired]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.user_exists',
                AsyncMock(return_value=True),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.create_user',
                AsyncMock(),
            ) as create_user,
            patch(
                'server.services.org_budget_service.LiteLlmManager.add_user_to_team',
                AsyncMock(),
            ) as add_user,
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            result = await service.run_budget_maintenance(budget_org.id)

    assert result['cycle_rolled'] is True
    create_user.assert_not_awaited()
    add_user.assert_awaited_once_with(str(user_id), str(budget_org.id), 30.0)
    assert settings.cycle_start_spend == 42.0
    assert settings.user_cycle_start_spend == {str(user_id): 0.0}
    assert settings.litellm_known_member_ids == [str(user_id)]
    sync_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cycle_roll_does_not_advance_when_membership_repair_fails(
    async_session_maker, budget_org
):
    user_id = uuid4()
    old_cycle_start = _current_cycle_start(datetime.now(UTC) - timedelta(days=40), 1)

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=old_cycle_start,
            cycle_start_spend=10.0,
            litellm_known_member_ids=[str(user_id)],
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(
                    return_value=_financial_data(team_spend=42.0, team_max_budget=110.0)
                ),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.user_exists',
                AsyncMock(return_value=False),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.add_user_to_team',
                AsyncMock(),
            ) as add_user,
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            result = await service.run_budget_maintenance(budget_org.id)

    assert result['skipped'] == 'litellm_membership_repair_failed'
    assert result['reconciliation_status'] == 'error'
    assert result['reconciliation_error']
    assert result['cycle_rolled'] is False
    assert settings.cycle_start_at.replace(tzinfo=UTC) == old_cycle_start
    assert settings.cycle_start_spend == 10.0
    assert settings.litellm_last_sync_status == 'error'
    add_user.assert_not_awaited()
    sync_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_budget_state_uses_litellm_cycle_spend(
    async_session_maker, budget_org
):
    user_id = uuid4()
    async with async_session_maker() as session:
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(
                    id=user_id,
                    current_org_id=budget_org.id,
                    email='member@example.com',
                    git_user_name='member',
                ),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=1,
                    monthly_limit=100.0,
                    default_user_monthly_limit=50.0,
                    cycle_start_at=datetime.now(UTC),
                    cycle_start_spend=40.0,
                    user_cycle_start_spend={str(user_id): 15.0},
                ),
            ]
        )
        await session.commit()

        financial_data = _financial_data(
            team_spend=130.0,
            members={str(user_id): (55.0, None, True)},
        )
        with patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(return_value=financial_data),
        ):
            state = await OrgBudgetService(session).get_budget_state(budget_org.id)

    assert state['current_spend'] == 90.0
    assert state['spend_status'] == 'live'
    assert state['users_total'] == 1
    assert state['users'][0]['current_spend'] == 40.0


@pytest.mark.asyncio
async def test_get_budget_state_reports_unmapped_litellm_spend(
    async_session_maker, budget_org
):
    service_account_id = 'sdk-service-account'
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                cycle_start_at=datetime.now(UTC),
                cycle_start_spend=5.0,
                user_cycle_start_spend={service_account_id: 2.0},
            )
        )
        await session.commit()

        with patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(
                return_value=_financial_data(
                    team_spend=17.0,
                    team_max_budget=105.0,
                    members={service_account_id: (14.0, 105.0, True)},
                )
            ),
        ):
            state = await OrgBudgetService(session).get_budget_state(budget_org.id)

    assert state['current_spend'] == 12.0
    assert state['unmapped_member_count'] == 1
    assert state['unmapped_spend'] == 12.0


@pytest.mark.asyncio
async def test_get_budget_state_uses_last_known_good_snapshot_on_fetch_failure(
    async_session_maker, budget_org
):
    observed_at = datetime.now(UTC) - timedelta(minutes=5)
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                cycle_start_at=datetime.now(UTC),
                cycle_start_spend=40.0,
                litellm_last_spend_snapshot_at=observed_at,
                litellm_last_team_spend=130.0,
                litellm_last_member_spend={},
            )
        )
        await session.commit()

        get_financial_data = AsyncMock(side_effect=TimeoutError('timed out'))
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                get_financial_data,
            ),
            patch(
                'server.services.org_budget_service.asyncio.sleep',
                AsyncMock(),
            ),
        ):
            state = await OrgBudgetService(session).get_budget_state(budget_org.id)

    assert state['current_spend'] == 90.0
    assert state['spend_status'] == 'stale'
    assert state['spend_observed_at'] == observed_at
    assert get_financial_data.await_count == 3


@pytest.mark.asyncio
async def test_get_budget_state_retries_transient_fetch_failure(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                cycle_start_at=datetime.now(UTC),
                cycle_start_spend=40.0,
            )
        )
        await session.commit()

        get_financial_data = AsyncMock(
            side_effect=[
                TimeoutError('timed out'),
                _financial_data(team_spend=130.0),
            ]
        )
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                get_financial_data,
            ),
            patch(
                'server.services.org_budget_service.asyncio.sleep',
                AsyncMock(),
            ),
        ):
            state = await OrgBudgetService(session).get_budget_state(budget_org.id)

    assert state['current_spend'] == 90.0
    assert state['spend_status'] == 'live'
    assert get_financial_data.await_count == 2


@pytest.mark.asyncio
async def test_get_budget_state_never_turns_malformed_data_into_zero(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                cycle_start_at=datetime.now(UTC),
                cycle_start_spend=40.0,
            )
        )
        await session.commit()

        get_financial_data = AsyncMock(return_value={'members': {}})
        with patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            get_financial_data,
        ):
            state = await OrgBudgetService(session).get_budget_state(budget_org.id)

    assert state['current_spend'] is None
    assert state['spend_status'] == 'unavailable'
    assert state['spend_observed_at'] is None
    get_financial_data.assert_awaited_once()


LITELLM_FINANCIAL_DATA = (
    'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data'
)


def _enabled_budget_settings(org_id, **overrides) -> OrgBudgetSettings:
    return OrgBudgetSettings(
        **{
            'org_id': org_id,
            'enabled': True,
            'reset_day': 1,
            'default_user_monthly_limit': 50.0,
            'cycle_start_at': datetime.now(UTC),
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_my_budget_is_not_enabled_for_personal_org_without_creating_settings(
    async_session_maker, personal_org
):
    # Arrange
    async with async_session_maker() as session:
        service = OrgBudgetService(session)

        # Act
        budget = await service.get_my_budget(personal_org.id, personal_org.id)

        # Assert
        result = await session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == personal_org.id)
        )
    assert budget == {'enabled': False}
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_my_budget_is_not_enabled_for_unconfigured_org_without_creating_settings(
    async_session_maker, budget_org
):
    # Arrange
    async with async_session_maker() as session:
        service = OrgBudgetService(session)

        # Act
        budget = await service.get_my_budget(budget_org.id, uuid4())

        # Assert
        result = await session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == budget_org.id)
        )
    assert budget == {'enabled': False}
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_my_budget_is_not_enabled_when_admin_turned_budgets_off(
    async_session_maker, budget_org
):
    # Arrange
    async with async_session_maker() as session:
        session.add(_enabled_budget_settings(budget_org.id, enabled=False))
        await session.commit()

        # Act
        budget = await OrgBudgetService(session).get_my_budget(budget_org.id, uuid4())

    # Assert
    assert budget == {'enabled': False}


@pytest.mark.asyncio
async def test_my_budget_enabled_check_does_not_read_spend(
    async_session_maker, budget_org
):
    # Arrange
    async with async_session_maker() as session:
        session.add(_enabled_budget_settings(budget_org.id))
        await session.commit()

        # Act
        with patch(LITELLM_FINANCIAL_DATA, AsyncMock()) as get_financial_data:
            budget = await OrgBudgetService(session).get_my_budget(
                budget_org.id, uuid4(), include_spend=False
            )

    # Assert
    assert budget == {'enabled': True}
    get_financial_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_my_budget_reports_org_default_and_only_own_cycle_spend(
    async_session_maker, budget_org
):
    # Arrange
    user_id, teammate_id = uuid4(), uuid4()
    financial_data = _financial_data(
        team_spend=154.0,
        members={
            str(user_id): (55.0, None, True),
            str(teammate_id): (99.0, None, True),
        },
    )
    async with async_session_maker() as session:
        session.add(
            _enabled_budget_settings(
                budget_org.id,
                user_cycle_start_spend={str(user_id): 15.0, str(teammate_id): 1.0},
            )
        )
        await session.commit()

        # Act
        with patch(LITELLM_FINANCIAL_DATA, AsyncMock(return_value=financial_data)):
            budget = await OrgBudgetService(session).get_my_budget(
                budget_org.id, user_id
            )

    # Assert
    assert budget['monthly_limit'] == 50.0
    assert budget['is_override'] is False
    assert budget['limit_updated_at'] is None
    assert budget['current_spend'] == 40.0
    assert budget['spend_status'] == 'live'
    assert budget['cycle_end_at'] > budget['cycle_start_at']


@pytest.mark.asyncio
async def test_my_budget_reports_admin_override_with_the_date_it_was_set(
    async_session_maker, budget_org
):
    # Arrange
    user_id = uuid4()
    async with async_session_maker() as session:
        session.add(User(id=user_id, current_org_id=budget_org.id))
        await session.flush()
        session.add_all(
            [
                _enabled_budget_settings(budget_org.id),
                OrgUserBudgetOverride(
                    org_id=budget_org.id, user_id=user_id, monthly_limit=500.0
                ),
            ]
        )
        await session.commit()

        # Act
        with patch(LITELLM_FINANCIAL_DATA, AsyncMock(return_value=_financial_data())):
            budget = await OrgBudgetService(session).get_my_budget(
                budget_org.id, user_id
            )

    # Assert
    assert budget['monthly_limit'] == 500.0
    assert budget['is_override'] is True
    assert budget['limit_updated_at'] is not None


@pytest.mark.asyncio
async def test_my_budget_reports_no_limit_when_admin_exempted_the_user(
    async_session_maker, budget_org
):
    # Arrange
    user_id = uuid4()
    async with async_session_maker() as session:
        session.add(User(id=user_id, current_org_id=budget_org.id))
        await session.flush()
        session.add_all(
            [
                _enabled_budget_settings(budget_org.id),
                OrgUserBudgetOverride(
                    org_id=budget_org.id, user_id=user_id, is_disabled=True
                ),
            ]
        )
        await session.commit()

        # Act
        with patch(LITELLM_FINANCIAL_DATA, AsyncMock(return_value=_financial_data())):
            budget = await OrgBudgetService(session).get_my_budget(
                budget_org.id, user_id
            )

    # Assert
    assert budget['monthly_limit'] is None
    assert budget['is_disabled'] is True


@pytest.mark.asyncio
async def test_my_budget_reports_spend_unavailable_instead_of_failing(
    async_session_maker, budget_org
):
    # Arrange
    async with async_session_maker() as session:
        session.add(_enabled_budget_settings(budget_org.id))
        await session.commit()

        # Act
        with patch(LITELLM_FINANCIAL_DATA, AsyncMock(side_effect=ValueError('down'))):
            budget = await OrgBudgetService(session).get_my_budget(
                budget_org.id, uuid4()
            )

    # Assert
    assert budget['monthly_limit'] == 50.0
    assert budget['current_spend'] is None
    assert budget['spend_status'] == 'unavailable'


@pytest.mark.asyncio
async def test_enabling_budget_requires_fresh_snapshot_and_preserves_baseline(
    async_session_maker, budget_org
):
    old_cycle_start = datetime.now(UTC) - timedelta(days=10)
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=False,
                reset_day=1,
                monthly_limit=None,
                cycle_start_at=old_cycle_start,
                cycle_start_spend=77.0,
            )
        )
        await session.commit()

        with patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(side_effect=TimeoutError('timed out')),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await OrgBudgetService(session).update_budget_settings(
                    budget_org.id,
                    OrgBudgetSettingsUpdate(enabled=True, monthly_limit=100.0),
                )
        await session.rollback()
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == budget_org.id)
        )

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert settings is not None
    assert settings.enabled is False
    assert settings.cycle_start_spend == 77.0
    assert settings.cycle_start_at.replace(tzinfo=UTC) == old_cycle_start


@pytest.mark.asyncio
async def test_maintenance_does_not_roll_cycle_without_fresh_snapshot(
    async_session_maker, budget_org
):
    old_cycle_start = _current_cycle_start(datetime.now(UTC) - timedelta(days=40), 1)
    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            cycle_start_at=old_cycle_start,
            cycle_start_spend=77.0,
        )
        session.add(settings)
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=TimeoutError('timed out')),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ) as update_team,
        ):
            result = await OrgBudgetService(session).run_budget_maintenance(
                budget_org.id
            )

    assert result['skipped'] == 'litellm_spend_unavailable'
    assert result['cycle_rolled'] is False
    assert result['current_spend'] is None
    assert settings.cycle_start_at.replace(tzinfo=UTC) == old_cycle_start
    assert settings.cycle_start_spend == 77.0
    assert settings.litellm_last_sync_status == 'error'
    update_team.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_maintenance_alerts_on_litellm_spend(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        cycle_start = _current_cycle_start(datetime.now(UTC), 1)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            cycle_start_at=cycle_start,
            cycle_start_spend=10.0,
        )
        threshold = OrgBudgetThreshold(
            org_id=budget_org.id,
            percentage=80,
            email_enabled=True,
            slack_enabled=False,
        )
        session.add_all([settings, threshold])
        await session.commit()

        financial_data = _financial_data(team_spend=95.0)
        service = OrgBudgetService(session)
        service._send_alerts = AsyncMock()
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(return_value=financial_data),
            ),
            patch.object(
                service,
                '_sync_litellm_budgets',
                AsyncMock(),
            ),
        ):
            result = await service.run_budget_maintenance(budget_org.id)

    assert result['current_spend'] == 85.0
    service._send_alerts.assert_awaited_once()
    assert service._send_alerts.await_args.args[3] == 85.0
    assert service._send_alerts.await_args.args[4] == 85.0


@pytest.mark.asyncio
async def test_sync_litellm_budgets_updates_team_and_members(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        now = datetime.now(UTC)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            slack_channel=None,
            slack_team_id=None,
            cycle_start_at=now,
            cycle_start_spend=20.0,
        )

        disabled_user_id = uuid4()
        override_user_id = uuid4()
        default_user_id = uuid4()

        session.add(Role(id=1, name='member', rank=1))
        for user_id in (disabled_user_id, override_user_id, default_user_id):
            session.add(
                User(
                    id=user_id,
                    current_org_id=budget_org.id,
                    email=f'{user_id}@example.com',
                )
            )
            session.add(
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                )
            )
        session.add(settings)
        await session.commit()

        overrides = [
            OrgUserBudgetOverride(
                org_id=budget_org.id,
                user_id=disabled_user_id,
                monthly_limit=None,
                is_disabled=True,
            ),
            OrgUserBudgetOverride(
                org_id=budget_org.id,
                user_id=override_user_id,
                monthly_limit=50.0,
                is_disabled=False,
            ),
        ]

        financial_data = _financial_data(
            team_spend=20.0,
            team_max_budget=100.0,
            members={
                str(disabled_user_id): (12.0, 100.0, True),
                str(override_user_id): (7.0, 100.0, True),
                str(default_user_id): (5.0, 100.0, True),
            },
        )
        readback = _financial_data(
            team_spend=20.0,
            team_max_budget=120.0,
            members={
                str(disabled_user_id): (12.0, 120.0, True),
                str(override_user_id): (7.0, 57.0, False),
                str(default_user_id): (5.0, 35.0, False),
            },
        )

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[financial_data, readback]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ) as update_team,
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await service._sync_litellm_budgets(budget_org.id, settings, overrides)

        update_team.assert_awaited_once_with(
            str(budget_org.id),
            team_alias=None,
            max_budget=120.0,
        )
        update_user.assert_has_awaits(
            [
                call(
                    str(disabled_user_id),
                    str(budget_org.id),
                    max_budget=None,
                    clear_budget=True,
                ),
                call(
                    str(override_user_id),
                    str(budget_org.id),
                    max_budget=57.0,
                    clear_budget=False,
                ),
                call(
                    str(default_user_id),
                    str(budget_org.id),
                    max_budget=35.0,
                    clear_budget=False,
                ),
            ],
            any_order=True,
        )

        assert settings.user_cycle_start_spend == {
            str(disabled_user_id): 12.0,
            str(override_user_id): 7.0,
            str(default_user_id): 5.0,
        }
        assert settings.litellm_last_sync_status == 'success'
        assert settings.litellm_last_sync_error is None
        assert settings.litellm_last_sync_at is not None


@pytest.mark.asyncio
async def test_sync_litellm_budgets_reports_member_readback_mismatch(
    async_session_maker, budget_org
):
    user_id = uuid4()
    before = {
        'team_max_budget': 80.0,
        'team_spend': 20.0,
        'members': {
            str(user_id): {
                'spend': 5.0,
                'max_budget': 80.0,
                'uses_shared_budget': True,
            }
        },
    }
    after = {
        'team_max_budget': 120.0,
        'team_spend': 20.0,
        'members': {
            str(user_id): {
                'spend': 5.0,
                'max_budget': 120.0,
                'uses_shared_budget': True,
            }
        },
    }

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=20.0,
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(
                    id=user_id,
                    current_org_id=budget_org.id,
                    email='mismatch@example.com',
                ),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()
        service = OrgBudgetService(session)
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ) as get_financial_data,
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ),
        ):
            result = await service._sync_litellm_budgets(budget_org.id, settings, [])

    assert result is not None
    assert result.team_spend == after['team_spend']
    assert get_financial_data.await_count == 2
    assert settings.litellm_last_sync_status == 'error'
    assert settings.litellm_last_sync_error is not None
    assert f'member_budget_mismatch: {user_id}' in settings.litellm_last_sync_error


@pytest.mark.asyncio
async def test_sync_litellm_budgets_reports_missing_governed_member(
    async_session_maker, budget_org
):
    user_id = uuid4()
    before = _financial_data(team_spend=20.0, team_max_budget=100.0)
    after = _financial_data(team_spend=20.0, team_max_budget=120.0)

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=20.0,
            user_cycle_start_spend={str(user_id): 5.0},
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(
                    id=user_id,
                    current_org_id=budget_org.id,
                    email='missing@example.com',
                ),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )

    assert settings.litellm_last_sync_status == 'error'
    assert settings.litellm_last_sync_error is not None
    assert f'member_missing_from_litellm: {user_id}' in (
        settings.litellm_last_sync_error
    )
    assert settings.user_cycle_start_spend == {str(user_id): 5.0}
    update_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_recovers_known_member_cycle_baseline_when_missing(
    async_session_maker, budget_org
):
    user_id = uuid4()
    # Legacy upgrade shape: migration 156 marked the member known while
    # migration 149 left no baseline, and LiteLLM still enforces a stale cap
    # below the member's cumulative spend.
    before = _financial_data(
        team_spend=20.0,
        team_max_budget=100.0,
        members={str(user_id): (8.0, 5.0, False)},
    )
    after = _financial_data(
        team_spend=20.0,
        team_max_budget=120.0,
        members={str(user_id): (8.0, 38.0, False)},
    )

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=20.0,
            user_cycle_start_spend={},
            litellm_known_member_ids=[str(user_id)],
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )

    update_user.assert_awaited_once_with(
        str(user_id),
        str(budget_org.id),
        max_budget=38.0,
        clear_budget=False,
    )
    assert settings.user_cycle_start_spend == {str(user_id): 8.0}
    assert settings.litellm_known_member_ids == [str(user_id)]
    assert settings.litellm_last_sync_status == 'success'
    assert settings.litellm_last_sync_error is None


@pytest.mark.asyncio
async def test_sync_recovered_baseline_is_not_renewed_on_later_sync(
    async_session_maker, budget_org
):
    user_id = uuid4()
    financial_data = AsyncMock(
        side_effect=[
            _financial_data(
                team_spend=20.0,
                team_max_budget=100.0,
                members={str(user_id): (8.0, 5.0, False)},
            ),
            _financial_data(
                team_spend=20.0,
                team_max_budget=120.0,
                members={str(user_id): (8.0, 38.0, False)},
            ),
            _financial_data(
                team_spend=32.0,
                team_max_budget=120.0,
                members={str(user_id): (20.0, 38.0, False)},
            ),
            _financial_data(
                team_spend=32.0,
                team_max_budget=120.0,
                members={str(user_id): (20.0, 38.0, False)},
            ),
        ]
    )

    with (
        patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            financial_data,
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_team',
            AsyncMock(),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
            AsyncMock(),
        ) as update_user,
    ):
        async with async_session_maker() as session:
            settings = OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                default_user_monthly_limit=30.0,
                cycle_start_at=datetime.now(UTC),
                cycle_start_spend=20.0,
                user_cycle_start_spend={},
                litellm_known_member_ids=[str(user_id)],
            )
            session.add_all(
                [
                    Role(id=1, name='member', rank=1),
                    User(id=user_id, current_org_id=budget_org.id),
                    OrgMember(
                        org_id=budget_org.id,
                        user_id=user_id,
                        role_id=1,
                        llm_api_key='test-api-key',
                        status='active',
                    ),
                    settings,
                ]
            )
            await session.commit()

            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )
            await session.commit()

        async with async_session_maker() as session:
            result = await session.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
            settings = result.scalar_one()
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )
            await session.commit()

            assert settings.user_cycle_start_spend == {str(user_id): 8.0}
            assert settings.litellm_last_sync_status == 'success'

    assert [
        await_call.kwargs['max_budget'] for await_call in update_user.await_args_list
    ] == [
        38.0,
        38.0,
    ]


@pytest.mark.asyncio
async def test_sync_initializes_baseline_for_member_added_after_migration(
    async_session_maker, budget_org
):
    user_id = uuid4()
    before = _financial_data(
        team_spend=20.0,
        team_max_budget=100.0,
        members={str(user_id): (8.0, 100.0, True)},
    )
    after = _financial_data(
        team_spend=20.0,
        team_max_budget=120.0,
        members={str(user_id): (8.0, 38.0, False)},
    )

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=20.0,
            user_cycle_start_spend={},
            litellm_known_member_ids=[],
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )

    update_user.assert_awaited_once_with(
        str(user_id),
        str(budget_org.id),
        max_budget=38.0,
        clear_budget=False,
    )
    assert settings.user_cycle_start_spend == {str(user_id): 8.0}
    assert settings.litellm_known_member_ids == [str(user_id)]
    assert settings.litellm_last_sync_status == 'success'


@pytest.mark.asyncio
async def test_sync_preserves_unmapped_member_cycle_baseline(
    async_session_maker, budget_org
):
    service_account_id = 'sdk-service-account'
    before = _financial_data(
        team_spend=20.0,
        team_max_budget=100.0,
        members={service_account_id: (8.0, 100.0, True)},
    )
    after = _financial_data(
        team_spend=20.0,
        team_max_budget=120.0,
        members={service_account_id: (8.0, 120.0, True)},
    )

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=20.0,
            user_cycle_start_spend={service_account_id: 3.0},
        )
        session.add(settings)
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )

    update_user.assert_not_awaited()
    assert settings.user_cycle_start_spend == {service_account_id: 3.0}
    assert settings.litellm_last_sync_status == 'success'


@pytest.mark.asyncio
async def test_sync_litellm_budgets_keeps_member_cap_stable_across_sessions(
    async_session_maker, budget_org
):
    user_id = uuid4()
    cycle_start = datetime.now(UTC)
    financial_data = AsyncMock(
        side_effect=[
            _financial_data(
                team_spend=20.0,
                team_max_budget=120.0,
                members={str(user_id): (7.0, 120.0, True)},
            ),
            _financial_data(
                team_spend=20.0,
                team_max_budget=120.0,
                members={str(user_id): (7.0, 57.0, False)},
            ),
            _financial_data(
                team_spend=36.0,
                team_max_budget=120.0,
                members={str(user_id): (23.0, 57.0, False)},
            ),
            _financial_data(
                team_spend=36.0,
                team_max_budget=120.0,
                members={str(user_id): (23.0, 57.0, False)},
            ),
        ]
    )

    with (
        patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            financial_data,
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_team',
            AsyncMock(),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
            AsyncMock(),
        ) as update_user,
    ):
        async with async_session_maker() as session:
            settings = OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                default_user_monthly_limit=50.0,
                cycle_start_at=cycle_start,
                cycle_start_spend=20.0,
            )
            session.add_all(
                [
                    Role(id=1, name='member', rank=1),
                    User(
                        id=user_id,
                        current_org_id=budget_org.id,
                        email='stable@example.com',
                    ),
                    OrgMember(
                        org_id=budget_org.id,
                        user_id=user_id,
                        role_id=1,
                        llm_api_key='test-api-key',
                        status='active',
                    ),
                    settings,
                ]
            )
            await session.commit()

            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )
            await session.commit()

        async with async_session_maker() as session:
            result = await session.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
            settings = result.scalar_one()
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )
            await session.commit()

            assert settings.user_cycle_start_spend == {str(user_id): 7.0}

    assert [
        await_call.kwargs['max_budget'] for await_call in update_user.await_args_list
    ] == [
        57.0,
        57.0,
    ]


@pytest.mark.asyncio
async def test_sync_litellm_budgets_skips_passive_disabled_team_org(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=False,
            reset_day=1,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=0.0,
        )

        with patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(),
        ) as get_financial_data:
            await service._sync_litellm_budgets(budget_org.id, settings, [])

    get_financial_data.assert_not_awaited()
    assert settings.litellm_last_sync_status == 'skipped'


@pytest.mark.asyncio
async def test_sync_litellm_budgets_clears_explicitly_disabled_team_org_cap(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=False,
            reset_day=1,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=0.0,
        )

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(return_value=_financial_data()),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ) as update_team,
        ):
            await service._sync_litellm_budgets(
                budget_org.id, settings, [], clear_disabled=True
            )

    update_team.assert_awaited_once_with(
        str(budget_org.id),
        team_alias=None,
        max_budget=None,
        clear_budget=True,
    )
    assert settings.litellm_last_sync_status == 'success'


@pytest.mark.asyncio
async def test_maybe_send_alerts_tracks_thresholds(async_session_maker, budget_org):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        now = datetime.now(UTC)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=None,
            slack_channel=None,
            slack_team_id=None,
            cycle_start_at=now,
            cycle_start_spend=0.0,
        )
        cycle_start = now
        threshold_80 = OrgBudgetThreshold(
            org_id=budget_org.id,
            percentage=80,
            email_enabled=True,
            slack_enabled=False,
        )
        threshold_90 = OrgBudgetThreshold(
            org_id=budget_org.id,
            percentage=90,
            email_enabled=True,
            slack_enabled=False,
        )

        service.store.flush = AsyncMock()
        service._send_alerts = AsyncMock()

        await service._maybe_send_alerts(
            budget_org.id,
            settings,
            [threshold_80, threshold_90],
            current_spend=85.0,
            cycle_start=cycle_start,
        )

        service._send_alerts.assert_awaited_once()
        assert threshold_80.last_triggered_cycle_start == cycle_start
        assert threshold_80.last_triggered_at is not None
        assert threshold_90.last_triggered_at is None
        service.store.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_alerts_emails_and_slack(async_session_maker, budget_org):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=120.0,
            default_user_monthly_limit=None,
            slack_channel='alerts',
            slack_team_id='team-123',
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=0.0,
        )
        threshold = OrgBudgetThreshold(
            org_id=budget_org.id,
            percentage=90,
            email_enabled=True,
            slack_enabled=True,
        )

        service._get_org_name = AsyncMock(return_value='Acme')
        service._get_admin_emails = AsyncMock(return_value=['admin@example.com'])
        service._send_slack_alert = AsyncMock()

        with patch(
            'server.services.org_budget_service.SMTPEmailService.send_budget_alert_email',
            MagicMock(),
        ) as send_email:
            await service._send_alerts(
                budget_org.id,
                settings,
                threshold,
                current_spend=100.0,
                percentage=83.3,
            )

        send_email.assert_called_once_with(
            ['admin@example.com'],
            org_name='Acme',
            percentage=83.3,
            current_spend=100.0,
            monthly_limit=120.0,
            threshold=90,
        )
        service._send_slack_alert.assert_awaited_once_with(
            'Acme',
            settings,
            90,
            100.0,
            83.3,
        )


@pytest.mark.asyncio
@freeze_time('2026-06-15')
async def test_concurrent_maintenance_runs_roll_the_cycle_only_once(
    async_session_maker, budget_org
):
    # OrgBudgetMaintenanceProcessor opens one session per invocation, so two
    # overlapping maintenance runs for the same org each hold their own copy of the
    # settings row. Nothing locks that row and nothing compares-and-sets
    # cycle_start_at, so the second run still sees the pre-roll cycle and rolls
    # again, re-anchoring the baseline to its newer cumulative spend.
    reset_day = 1
    stale_cycle_start = _current_cycle_start(
        datetime.now(UTC) - timedelta(days=40), reset_day
    )
    async with async_session_maker() as setup:
        setup.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=reset_day,
                monthly_limit=250.0,
                cycle_start_at=stale_cycle_start,
                cycle_start_spend=10.0,
            )
        )
        await setup.commit()

    async with async_session_maker() as first, async_session_maker() as second:
        first_service = OrgBudgetService(first)
        second_service = OrgBudgetService(second)

        second_view = []

        async def _first_snapshot(*args, **kwargs):
            # The second worker loads the settings row while the first is still
            # awaiting LiteLLM, so it reads the cycle before the first commits.
            # Held in a list because the session's identity map is weak-keyed.
            second_view.append(await second_service.store.get_settings(budget_org.id))
            return BudgetFinancialSnapshotResult(
                snapshot=_snapshot(team_spend=42.5), status='live'
            )

        with (
            patch.object(
                first_service,
                '_get_financial_snapshot',
                AsyncMock(side_effect=_first_snapshot),
            ),
            patch.object(first_service, '_sync_litellm_budgets', AsyncMock()),
        ):
            first_result = await first_service.run_budget_maintenance(budget_org.id)
        await first.commit()

        with (
            patch.object(
                second_service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=_snapshot(team_spend=90.0), status='live'
                    )
                ),
            ),
            patch.object(second_service, '_sync_litellm_budgets', AsyncMock()),
        ):
            second_result = await second_service.run_budget_maintenance(budget_org.id)
        await second.commit()

    assert first_result['cycle_rolled'] is True

    async with async_session_maker() as check:
        settings = (
            await check.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
        ).scalar_one()

    # A cycle rolls at most once per reset period: the second run must not roll the
    # same cycle again, and must not re-anchor the baseline to its newer spend.
    assert second_result['cycle_rolled'] is False
    assert settings.cycle_start_spend == 42.5


@pytest.mark.asyncio
async def test_maintenance_survives_a_stored_reset_day_the_month_lacks(
    async_session_maker, budget_org
):
    # reset_day has no CHECK constraint (migration 133 stores a plain Integer) and
    # _current_cycle_start / _next_cycle_start build datetime(year, month, reset_day)
    # with no clamping, so a row holding 29-31 kills every cycle calculation in a
    # short month. The PATCH endpoint validates to {1, 15}, so such a row reaches
    # production only by a direct write, migration or data fix -- but nothing in the
    # service defends against one.
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=31,
                monthly_limit=250.0,
                cycle_start_at=datetime(2026, 1, 31, tzinfo=UTC),
                cycle_start_spend=10.0,
            )
        )
        await session.commit()

        service = OrgBudgetService(session)
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=_snapshot(team_spend=42.5), status='live'
                    )
                ),
            ),
            patch.object(service, '_sync_litellm_budgets', AsyncMock()),
        ):
            # The next cycle after January 31st is February 31st, which does not
            # exist. Cycle arithmetic must clamp to the month's last day or refuse
            # through the service's own error path -- never die on a raw ValueError.
            try:
                result = await service.run_budget_maintenance(budget_org.id)
            except ValueError as exc:
                pytest.fail(f'cycle arithmetic crashed on a stored reset_day: {exc}')

    assert result['cycle_start_at'] is not None


@pytest.mark.parametrize('reset_day', [-1, 0, 1, 15, 28, 29, 30, 31])
def test_cycle_boundaries_stay_ordered_for_any_stored_reset_day(reset_day):
    # reset_day is untrusted, so every value must yield a real date and a strictly
    # increasing sequence of cycle starts: _roll_cycle_if_needed advances only while
    # now >= next_cycle, and a boundary that repeats or moves backwards would either
    # wedge that loop or re-baseline cycle_start_spend twice for one period.
    cycle_start = _current_cycle_start(datetime(2026, 1, 15, tzinfo=UTC), reset_day)
    assert cycle_start <= datetime(2026, 1, 15, tzinfo=UTC)

    for _ in range(40):
        next_cycle = _next_cycle_start(cycle_start, reset_day)
        assert next_cycle > cycle_start
        cycle_start = next_cycle


@pytest.mark.parametrize(
    ('now', 'reset_day', 'expected_start', 'expected_next'),
    [
        # 1 and 15 are the only values the PATCH endpoint allows: the clamp is a no-op.
        (datetime(2026, 1, 15), 15, datetime(2026, 1, 15), datetime(2026, 2, 15)),
        (datetime(2026, 1, 15), 1, datetime(2026, 1, 1), datetime(2026, 2, 1)),
        # 31 clamps down to the last day a short month holds, and back up afterwards.
        (datetime(2026, 2, 28), 31, datetime(2026, 2, 28), datetime(2026, 3, 31)),
        (datetime(2026, 3, 15), 31, datetime(2026, 2, 28), datetime(2026, 3, 31)),
        # 0 and below clamp up to the 1st.
        (datetime(2026, 1, 15), 0, datetime(2026, 1, 1), datetime(2026, 2, 1)),
    ],
)
@freeze_time('2026-06-15')
def test_cycle_boundaries_land_on_the_day_the_month_holds(
    now, reset_day, expected_start, expected_next
):
    # Ordering alone is satisfied by a great many wrong clamps, so pin the dates the
    # boundaries actually land on: that the clamp reads the month's length rather than
    # the weekday of its 1st, that the guard compares against the clamped day, that the
    # previous-month branch clamps with its own month, and that a cycle pushed down to
    # the 28th in February climbs back to the 31st in March instead of ratcheting.
    cycle_start = _current_cycle_start(now.replace(tzinfo=UTC), reset_day)
    assert cycle_start == expected_start.replace(tzinfo=UTC)
    assert _next_cycle_start(cycle_start, reset_day) == expected_next.replace(
        tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_user_budget_row_rejects_personal_org_without_creating_settings(
    async_session_maker, personal_org
):
    # get_user_budget_row is the one budget entry point that never calls
    # _reject_personal_org: it goes straight to _get_or_create_settings, so reading a
    # user row for a personal workspace writes the very settings row migration 148
    # exists to delete. Its only route reaches it after upsert_user_override has
    # already rejected personal orgs, so today this is a missing guard rather than a
    # live leak -- nothing stops the next caller from reaching it unguarded.
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        with patch.object(
            service,
            '_get_financial_snapshot',
            AsyncMock(
                return_value=BudgetFinancialSnapshotResult(
                    snapshot=_snapshot(), status='live'
                )
            ),
        ):
            with pytest.raises(HTTPException) as row_error:
                await service.get_user_budget_row(personal_org.id, personal_org.id)

        result = await session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == personal_org.id)
        )

    assert row_error.value.status_code == status.HTTP_400_BAD_REQUEST
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_budget_row_rejection_is_recorded_by_quint_oracle(
    async_session_maker, personal_org
):
    # The guard passes its entry-point label to _reject_personal_org so the Quint
    # oracle records which operation rejected the personal workspace. That label is
    # the signal the Quint model keys on, and the sibling guards pass theirs too;
    # pin it here so a future refactor can't silently drop it.
    oracle = MagicMock()
    oracle.In = lambda value, domain: value
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        with patch('server.services.org_budget_service.quint_oracle', oracle):
            with pytest.raises(HTTPException):
                await service.get_user_budget_row(personal_org.id, personal_org.id)
    oracle.log.assert_called_once()
    assert oracle.log.call_args.args[0] == 'get_user_budget_row'


@pytest.mark.asyncio
async def test_override_write_is_durable_when_litellm_is_unreachable(
    async_session_maker, budget_org
):
    # When the post-write LiteLLM resync fails, upsert_user_override records the failure
    # rather than raising: _sync_litellm_budgets writes an 'error' sync row and returns.
    # This is deliberate. The override row must survive so the next run_budget_maintenance
    # pass can converge it, and the reconciliation state must report the write as
    # unapplied ('degraded') so the route can answer 503 while keeping the durable write.
    #
    # Raising here would roll the flushed override row back through the request-scoped
    # session (DbSessionInjector commits on clean return, rolls back on any exception),
    # discarding admin intent on a transient proxy timeout and leaving nothing for
    # maintenance to converge to. This test pins the "accept the write, report it as
    # unapplied" contract against that regression.
    user_id = uuid4()
    async with async_session_maker() as session:
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=1,
                    monthly_limit=250.0,
                    cycle_start_at=datetime.now(UTC),
                    cycle_start_spend=0.0,
                ),
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager'
                '.get_team_members_financial_data',
                AsyncMock(side_effect=TimeoutError('timed out')),
            ),
            patch(
                'server.services.org_budget_service.asyncio.sleep',
                AsyncMock(),
            ),
        ):
            # The write is accepted despite the proxy being unreachable.
            override = await service.upsert_user_override(
                budget_org.id, user_id, monthly_limit=50.0, is_disabled=False
            )

        assert override.monthly_limit == 50.0

        # The override row is durably persisted, not rolled back.
        persisted = (
            await session.execute(
                select(OrgUserBudgetOverride).where(
                    OrgUserBudgetOverride.org_id == budget_org.id,
                    OrgUserBudgetOverride.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        assert persisted is not None
        assert persisted.monthly_limit == 50.0

        # The failed sync is recorded so the route reports the write as unapplied (503).
        assert await service.get_reconciliation_state(budget_org.id) == 'degraded'
        settings = await service._get_or_create_settings(budget_org.id)
        assert settings.litellm_last_sync_status == 'error'


@pytest.mark.asyncio
@freeze_time('2026-06-15')
async def test_maintenance_recovers_a_multi_period_gap_without_renewing_the_cap(
    async_session_maker, budget_org
):
    # When maintenance has not run for an org in months -- a paused CronJob, an
    # outage, an org the scheduler only just started covering -- the first run settles
    # the anchor at the current period and re-anchors cycle_start_spend once. A second
    # run in the same period must be a no-op: if it rolled again it would re-anchor the
    # baseline to the newer cumulative total, forgiving spend incurred since recovery
    # and renewing the cap. This reproduces the regression from review PR #403.
    reset_day = 1
    stale_cycle_start = _current_cycle_start(
        datetime.now(UTC) - timedelta(days=100), reset_day
    )
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=reset_day,
                monthly_limit=250.0,
                cycle_start_at=stale_cycle_start,
                cycle_start_spend=0.0,
            )
        )
        await session.commit()

        service = OrgBudgetService(session)
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=_snapshot(team_spend=500.0), status='live'
                    )
                ),
            ),
            patch.object(service, '_sync_litellm_budgets', AsyncMock()),
        ):
            first = await service.run_budget_maintenance(budget_org.id)

        # The org spends another $1 after recovery; the cumulative total ticks up.
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=_snapshot(team_spend=501.0), status='live'
                    )
                ),
            ),
            patch.object(service, '_sync_litellm_budgets', AsyncMock()),
        ):
            second = await service.run_budget_maintenance(budget_org.id)

        settings = (
            await session.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
        ).scalar_one()

    # The first run catches up to the current period in a single jump.
    assert first['cycle_rolled'] is True
    assert settings.cycle_start_at == _current_cycle_start(datetime.now(UTC), reset_day)
    # The second run in the same period must not roll again or re-anchor the baseline,
    # so the $1 spent since recovery is preserved rather than forgiven.
    assert second['cycle_rolled'] is False
    assert settings.cycle_start_spend == 500.0
    assert second['current_spend'] == 1.0


@pytest.mark.asyncio
async def test_override_cap_is_never_written_below_the_cycle_baseline(
    async_session_maker, budget_org
):
    # Only the route's Pydantic model rejects a non-positive monthly_limit; the
    # service method and the store take whatever they are handed, and the column has
    # no CHECK constraint. _sync_litellm_budgets then writes baseline + limit straight
    # through, so a negative override hands LiteLLM a cap the member has already
    # exceeded and locks them out for the rest of the cycle with no spend of their own.
    user_id = uuid4()
    baseline = 100.0
    async with async_session_maker() as session:
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=1,
                    monthly_limit=250.0,
                    cycle_start_at=datetime.now(UTC),
                    cycle_start_spend=baseline,
                    user_cycle_start_spend={str(user_id): baseline},
                    litellm_known_member_ids=[str(user_id)],
                ),
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        before = _snapshot(
            team_spend=baseline, members={str(user_id): (baseline, None, True)}
        )
        # What LiteLLM holds once the clamped cap has been written: the member is
        # capped at their baseline, having spent nothing of their own this cycle.
        after = _snapshot(
            team_spend=baseline,
            team_max_budget=baseline + 250.0,
            members={str(user_id): (baseline, baseline, False)},
        )
        snapshots = [before, after]
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    side_effect=lambda *args, **kwargs: BudgetFinancialSnapshotResult(
                        snapshot=snapshots.pop(0) if snapshots else after,
                        status='live',
                    )
                ),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await service.upsert_user_override(
                budget_org.id, user_id, monthly_limit=-100.0, is_disabled=False
            )
            state = await service.get_budget_state(budget_org.id)

    written_caps = [
        call_args.kwargs['max_budget']
        for call_args in update_user.await_args_list
        if call_args.kwargs.get('max_budget') is not None
    ]

    # A cap below the cycle baseline is already exceeded the moment it is written.
    assert written_caps
    assert min(written_caps) >= baseline
    # Drift detection has to expect the clamped cap too, or the org is stuck
    # degraded — and degraded is a 503 on the budgets routes — for good.
    assert state['reconciliation_state'] == 'healthy'


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [-100.0, 0.0])
async def test_non_positive_org_default_limit_caps_members_at_their_baseline(
    async_session_maker, budget_org, limit
):
    # A non-positive default_user_monthly_limit reaches the same arithmetic by a
    # different route than an override, and zero is the clamp's own output: a
    # falsy-vs-None check anywhere on that path silently returns the member to the
    # shared team budget instead of capping them.
    user_id = uuid4()
    baseline = 100.0
    async with async_session_maker() as session:
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=1,
                    monthly_limit=250.0,
                    default_user_monthly_limit=limit,
                    cycle_start_at=datetime.now(UTC),
                    cycle_start_spend=baseline,
                    user_cycle_start_spend={str(user_id): baseline},
                    litellm_known_member_ids=[str(user_id)],
                ),
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        settings = await service._get_or_create_settings(budget_org.id)
        overrides = await service._get_overrides(budget_org.id)
        before = _snapshot(
            team_spend=baseline, members={str(user_id): (baseline, None, True)}
        )
        after = _snapshot(
            team_spend=baseline,
            team_max_budget=baseline + 250.0,
            members={str(user_id): (baseline, baseline, False)},
        )
        snapshots = [before, after]
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    side_effect=lambda *args, **kwargs: BudgetFinancialSnapshotResult(
                        snapshot=snapshots.pop(0) if snapshots else after,
                        status='live',
                    )
                ),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as update_user,
        ):
            await service._sync_litellm_budgets(budget_org.id, settings, overrides)
            state = await service.get_budget_state(budget_org.id)

    call_args = update_user.await_args_list[-1]
    # No allowance means no further spend this cycle: a private cap at the
    # baseline, not a fall-through to the shared team budget.
    assert call_args.kwargs['max_budget'] == baseline
    assert call_args.kwargs['clear_budget'] is False
    assert state['reconciliation_state'] == 'healthy'


@pytest.mark.asyncio
async def test_threshold_alerts_once_per_cycle_across_a_settings_edit(
    async_session_maker, budget_org, monkeypatch
):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.invalid')
    # _maybe_send_alerts dedupes on threshold.last_triggered_cycle_start, which lives
    # on the threshold row. An admin who edits the thresholds -- here just turning
    # Slack on for the 80% alert -- must not re-arm alerts inside the live cycle and
    # page the same admins again for spend they have already acknowledged.
    reset_day = 1
    cycle_start = datetime.now(UTC)
    async with async_session_maker() as session:
        session.add_all(
            [
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=reset_day,
                    monthly_limit=100.0,
                    cycle_start_at=cycle_start,
                    cycle_start_spend=0.0,
                ),
                OrgBudgetThreshold(
                    org_id=budget_org.id,
                    percentage=80,
                    email_enabled=True,
                    slack_enabled=False,
                ),
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        snapshot = _snapshot(team_spend=85.0)
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=snapshot, status='live'
                    )
                ),
            ),
            patch.object(
                service, '_sync_litellm_budgets', AsyncMock(return_value=snapshot)
            ),
            patch.object(service, '_send_alerts', AsyncMock()) as send_alerts,
            patch.object(
                service,
                '_get_slack_bot_token',
                AsyncMock(return_value='test-only-token'),
            ),
        ):
            # 85 of a 100 cap crosses the 80% threshold: the admins are paged.
            await service.run_budget_maintenance(budget_org.id)
            assert send_alerts.await_count == 1

            # An ordinary settings edit that only turns Slack on for that threshold.
            await service.update_budget_settings(
                budget_org.id,
                OrgBudgetSettingsUpdate(
                    slack_channel='#budget-alerts',
                    thresholds=[
                        OrgBudgetThresholdUpdate(
                            percentage=80, email_enabled=True, slack_enabled=True
                        )
                    ],
                ),
            )
            await session.commit()

            # Same cycle, same spend, nothing newly crossed.
            await service.run_budget_maintenance(budget_org.id)

    # Each threshold alerts once per cycle.
    assert send_alerts.await_count == 1


@pytest.mark.asyncio
async def test_threshold_added_mid_cycle_alerts_once_for_spend_already_past_it(
    async_session_maker, budget_org, monkeypatch
):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.invalid')
    # Adding a threshold below the current spend pages the admins for it right away,
    # once -- without re-arming the thresholds that already fired this cycle.
    cycle_start = datetime.now(UTC)
    async with async_session_maker() as session:
        session.add_all(
            [
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=1,
                    monthly_limit=100.0,
                    cycle_start_at=cycle_start,
                    cycle_start_spend=0.0,
                ),
                OrgBudgetThreshold(
                    org_id=budget_org.id,
                    percentage=80,
                    email_enabled=True,
                    slack_enabled=False,
                ),
            ]
        )
        await session.commit()

        service = OrgBudgetService(session)
        snapshot = _snapshot(team_spend=95.0)
        with (
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(
                    return_value=BudgetFinancialSnapshotResult(
                        snapshot=snapshot, status='live'
                    )
                ),
            ),
            patch.object(
                service, '_sync_litellm_budgets', AsyncMock(return_value=snapshot)
            ),
            patch.object(service, '_send_alerts', AsyncMock()) as send_alerts,
        ):
            await service.run_budget_maintenance(budget_org.id)
            assert [
                call_args.args[2].percentage
                for call_args in send_alerts.await_args_list
            ] == [80]

            await service.update_budget_settings(
                budget_org.id,
                OrgBudgetSettingsUpdate(
                    thresholds=[
                        OrgBudgetThresholdUpdate(
                            percentage=80, email_enabled=True, slack_enabled=False
                        ),
                        OrgBudgetThresholdUpdate(
                            percentage=90, email_enabled=True, slack_enabled=False
                        ),
                    ]
                ),
            )
            await session.commit()

            await service.run_budget_maintenance(budget_org.id)
            await service.run_budget_maintenance(budget_org.id)

    # The new 90% threshold pages once; the 80% one stays latched.
    assert [
        call_args.args[2].percentage for call_args in send_alerts.await_args_list
    ] == [80, 90]


async def _baseline_rows(session, org_id, cycle_start_at) -> dict[str, tuple]:
    result = await session.execute(
        select(OrgBudgetCycleBaseline)
        .where(OrgBudgetCycleBaseline.org_id == org_id)
        .where(OrgBudgetCycleBaseline.cycle_start_at == cycle_start_at)
    )
    return {
        row.user_id: (row.baseline_spend, row.source, row.observed_at)
        for row in result.scalars()
    }


@pytest.mark.asyncio
async def test_roll_cycle_records_live_rollover_baseline_rows(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=250.0,
            cycle_start_at=_current_cycle_start(now - timedelta(days=40), 1),
            cycle_start_spend=10.0,
            user_cycle_start_spend={'existing-user': 4.0},
        )
        session.add(settings)
        await session.commit()

        service = OrgBudgetService(session)
        snapshot = _snapshot(team_spend=42.5, members={'member': (8.0, None, True)})
        with patch.object(service, '_sync_litellm_budgets', AsyncMock()):
            assert await service._roll_cycle_if_needed(settings, [], [], snapshot)
        await session.commit()

        rows = await _baseline_rows(session, budget_org.id, settings.cycle_start_at)

    assert settings.user_cycle_start_spend == {'member': 8.0}
    assert rows == {'member': (8.0, 'live_rollover', snapshot.observed_at)}


@pytest.mark.asyncio
async def test_enabling_budget_replaces_the_cycle_baseline_rows(
    async_session_maker, budget_org
):
    user_id = str(uuid4())
    current_cycle = _current_cycle_start(datetime.now(UTC), 1)
    before = _financial_data(
        team_spend=20.0,
        team_max_budget=None,
        members={user_id: (5.0, None, True)},
    )
    after = _financial_data(
        team_spend=20.0,
        team_max_budget=120.0,
        members={user_id: (5.0, None, True)},
    )

    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=False,
                reset_day=1,
                monthly_limit=None,
                cycle_start_at=current_cycle - timedelta(days=40),
                cycle_start_spend=0.0,
            )
        )
        await session.commit()
        await OrgBudgetStore(session).record_cycle_baselines(
            budget_org.id,
            current_cycle,
            {user_id: 1.0},
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=current_cycle,
        )
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
        ):
            result = await OrgBudgetService(session).update_budget_settings(
                budget_org.id,
                OrgBudgetSettingsUpdate(enabled=True, monthly_limit=100.0),
            )
        await session.commit()

        settings = result['settings']
        rows = await _baseline_rows(session, budget_org.id, settings.cycle_start_at)

    assert settings.cycle_start_at.replace(tzinfo=UTC) == current_cycle
    assert settings.user_cycle_start_spend == {user_id: 5.0}
    assert rows[user_id][:2] == (5.0, 'enablement')


@pytest.mark.asyncio
async def test_sync_records_recovered_and_added_baseline_rows(
    async_session_maker, budget_org
):
    known_user_id = uuid4()
    new_user_id = uuid4()
    before = _financial_data(
        team_spend=20.0,
        team_max_budget=100.0,
        members={
            str(known_user_id): (8.0, 5.0, False),
            str(new_user_id): (3.0, 100.0, True),
        },
    )
    after = _financial_data(
        team_spend=20.0,
        team_max_budget=120.0,
        members={
            str(known_user_id): (8.0, 38.0, False),
            str(new_user_id): (3.0, 33.0, False),
        },
    )

    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=budget_org.id,
            enabled=True,
            reset_day=1,
            monthly_limit=100.0,
            default_user_monthly_limit=30.0,
            cycle_start_at=datetime.now(UTC),
            cycle_start_spend=20.0,
            user_cycle_start_spend={},
            litellm_known_member_ids=[str(known_user_id)],
        )
        session.add_all(
            [
                Role(id=1, name='member', rank=1),
                User(id=known_user_id, current_org_id=budget_org.id),
                User(id=new_user_id, current_org_id=budget_org.id),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=known_user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                OrgMember(
                    org_id=budget_org.id,
                    user_id=new_user_id,
                    role_id=1,
                    llm_api_key='test-api-key',
                    status='active',
                ),
                settings,
            ]
        )
        await session.commit()

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_team',
                AsyncMock(),
            ),
            patch(
                'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ),
        ):
            await OrgBudgetService(session)._sync_litellm_budgets(
                budget_org.id, settings, []
            )
        await session.commit()

        rows = await _baseline_rows(session, budget_org.id, settings.cycle_start_at)

    assert settings.litellm_last_sync_status == 'success'
    assert settings.user_cycle_start_spend == {
        str(known_user_id): 8.0,
        str(new_user_id): 3.0,
    }
    assert rows[str(known_user_id)][:2] == (8.0, 'upgrade_recovery')
    assert rows[str(new_user_id)][:2] == (3.0, 'member_added')


@pytest.mark.asyncio
async def test_settings_loader_prefers_baseline_rows_and_imports_json_only_keys(
    async_session_maker, budget_org
):
    cycle_start = _current_cycle_start(datetime.now(UTC), 1)
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=1,
                monthly_limit=100.0,
                cycle_start_at=cycle_start,
                cycle_start_spend=20.0,
                user_cycle_start_spend={'a': 1.0, 'b': 2.0},
            )
        )
        await session.commit()
        await OrgBudgetStore(session).record_cycle_baselines(
            budget_org.id,
            cycle_start,
            {'a': 5.0},
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=cycle_start,
        )
        await session.commit()

        service = OrgBudgetService(session)
        settings = await service._get_or_create_settings(budget_org.id)
        await session.commit()
        first_rows = await _baseline_rows(session, budget_org.id, cycle_start)

        settings = await service._get_or_create_settings(budget_org.id)
        await session.commit()
        second_rows = await _baseline_rows(session, budget_org.id, cycle_start)

    assert settings.user_cycle_start_spend == {'a': 5.0, 'b': 2.0}
    assert first_rows['a'][:2] == (5.0, 'live_rollover')
    assert first_rows['b'][:2] == (2.0, 'imported')
    assert second_rows == first_rows


@pytest.mark.asyncio
# real_asyncio so the event loop's own timers still advance under the frozen clock;
# this test waits on one to prove the second run is blocked.
@freeze_time('2026-06-15', real_asyncio=True)
async def test_roll_cycle_blocks_a_second_run_until_the_first_commits(
    async_session_maker, budget_org
):
    reset_day = 1
    stale_cycle_start = _current_cycle_start(
        datetime.now(UTC) - timedelta(days=40), reset_day
    )
    async with async_session_maker() as setup:
        setup.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=True,
                reset_day=reset_day,
                monthly_limit=250.0,
                cycle_start_at=stale_cycle_start,
                cycle_start_spend=10.0,
            )
        )
        await setup.commit()

    async with async_session_maker() as first, async_session_maker() as second:
        first_service = OrgBudgetService(first)
        second_service = OrgBudgetService(second)
        first_settings = await first_service.store.get_settings(budget_org.id)
        second_settings = await second_service.store.get_settings(budget_org.id)

        with patch.object(first_service, '_sync_litellm_budgets', AsyncMock()):
            assert await first_service._roll_cycle_if_needed(
                first_settings, [], [], _snapshot(team_spend=42.5)
            )

        with patch.object(second_service, '_sync_litellm_budgets', AsyncMock()):
            second_roll = asyncio.create_task(
                second_service._roll_cycle_if_needed(
                    second_settings, [], [], _snapshot(team_spend=90.0)
                )
            )
            try:
                done, _ = await asyncio.wait({second_roll}, timeout=1.0)
                assert not done, (
                    'second run read the anchor while the first held the lock'
                )

                await first.commit()
                assert await asyncio.wait_for(second_roll, timeout=10) is False
            finally:
                # A failed assertion must not leave a task using a closing session.
                second_roll.cancel()
                await asyncio.gather(second_roll, return_exceptions=True)

        # Losing the race must also refresh the loser's stale copy: alerts, the
        # LiteLLM sync and the returned cycle all read this object afterwards. The
        # winner settled the anchor at the current period, so the loser sees that.
        assert second_settings.cycle_start_at.replace(
            tzinfo=UTC
        ) == _current_cycle_start(datetime.now(UTC), reset_day)
        assert second_settings.cycle_start_spend == 42.5
        await second.commit()

    async with async_session_maker() as check:
        settings = (
            await check.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
        ).scalar_one()
    assert settings.cycle_start_spend == 42.5


@pytest.mark.asyncio
@freeze_time('2026-06-15')
async def test_roll_cycle_reads_only_this_orgs_anchor(async_session_maker, budget_org):
    reset_day = 1
    due_cycle_start = _current_cycle_start(
        datetime.now(UTC) - timedelta(days=40), reset_day
    )
    other_org_id = uuid4()
    async with async_session_maker() as setup:
        setup.add(
            Org(
                id=other_org_id,
                name=f'test-org-{other_org_id}',
                org_version=ORG_SETTINGS_VERSION,
            )
        )
        await setup.flush()
        setup.add_all(
            [
                OrgBudgetSettings(
                    org_id=other_org_id,
                    enabled=True,
                    reset_day=reset_day,
                    monthly_limit=250.0,
                    cycle_start_at=_current_cycle_start(datetime.now(UTC), reset_day),
                    cycle_start_spend=7.0,
                ),
                OrgBudgetSettings(
                    org_id=budget_org.id,
                    enabled=True,
                    reset_day=reset_day,
                    monthly_limit=250.0,
                    cycle_start_at=due_cycle_start,
                    cycle_start_spend=10.0,
                ),
            ]
        )
        await setup.commit()

    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        settings = await service.store.get_settings(budget_org.id)
        with patch.object(service, '_sync_litellm_budgets', AsyncMock()):
            rolled = await service._roll_cycle_if_needed(
                settings, [], [], _snapshot(team_spend=42.5)
            )
        await session.commit()

    assert rolled is True
    assert settings.cycle_start_at.replace(tzinfo=UTC) == _current_cycle_start(
        datetime.now(UTC), reset_day
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'users_search,expected',
    [
        ('%', set()),
        ('_', {'bob_smith@example.com'}),
        ('\\', {'dave\\ops@example.com'}),
        ('alice', {'alice@example.com'}),
    ],
)
async def test_budget_user_search_treats_metacharacters_literally(
    async_session_maker, budget_org, users_search, expected
):
    """The budget page search box must narrow the roster, not widen it."""
    emails = ['alice@example.com', 'bob_smith@example.com', 'dave\\ops@example.com']
    async with async_session_maker() as session:
        role = Role(name='member', rank=1)
        session.add(role)
        await session.flush()
        for i, email in enumerate(emails):
            user_id = uuid4()
            session.add_all(
                [
                    User(id=user_id, current_org_id=budget_org.id, email=email),
                    OrgMember(
                        org_id=budget_org.id,
                        user_id=user_id,
                        role_id=role.id,
                        llm_api_key=f'test-key-{i}',
                        status='active',
                    ),
                ]
            )
        session.add(OrgBudgetSettings(org_id=budget_org.id, enabled=True))
        await session.commit()

    async with async_session_maker() as session:
        settings = (
            await session.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
        ).scalar_one()
        rows, total = await OrgBudgetService(session)._build_user_budget_rows(
            budget_org.id,
            settings,
            None,
            users_page=1,
            users_per_page=50,
            users_search=users_search,
            users_status=None,
        )

    assert {row['user_email'] for row in rows} == expected
    assert total == len(expected)


def _patch_stale_first_read(service: OrgBudgetService):
    """Make the service's first settings read miss, and later ones hit Postgres.

    This is the loser of the race: it looked before the winner had inserted, so
    it goes on to insert itself, and the recovery re-read that follows runs
    against the real row.
    """
    real_get_settings = service.store.get_settings
    already_read = []

    async def _stale_then_real(org_id):
        if not already_read:
            already_read.append(org_id)
            return None
        return await real_get_settings(org_id)

    return patch.object(
        service.store, 'get_settings', AsyncMock(side_effect=_stale_then_real)
    )


@pytest.mark.asyncio
async def test_settings_row_is_created_once_when_two_requests_race(
    async_session_maker, budget_org
):
    # _get_or_create_settings reads the settings row and then inserts one with no lock
    # and no ON CONFLICT, while OrgBudgetSettings.org_id is unique. Two requests that
    # both find no row -- the maintenance CronJob covering an org for the first time
    # while an admin opens its budgets page -- both insert, and the loser dies on the
    # unique constraint instead of using the row the winner just created.
    async with async_session_maker() as first, async_session_maker() as second:
        first_service = OrgBudgetService(first)
        second_service = OrgBudgetService(second)

        snapshot = _snapshot(team_spend=0.0)
        snapshot_result = BudgetFinancialSnapshotResult(
            snapshot=snapshot, status='live'
        )
        with (
            patch.object(
                first_service,
                '_get_financial_snapshot',
                AsyncMock(return_value=snapshot_result),
            ),
            patch.object(first_service, '_sync_litellm_budgets', AsyncMock()),
            patch.object(
                second_service,
                '_get_financial_snapshot',
                AsyncMock(return_value=snapshot_result),
            ),
            patch.object(second_service, '_sync_litellm_budgets', AsyncMock()),
        ):
            # The winner creates the row and commits it.
            await first_service.run_budget_maintenance(budget_org.id)
            await first.commit()

            with _patch_stale_first_read(second_service):
                # The loser proceeds on its stale read and tries to create it again.
                try:
                    await second_service.run_budget_maintenance(budget_org.id)
                    await second.commit()
                except IntegrityError as exc:
                    pytest.fail(
                        f'second request crashed creating a settings row that already '
                        f'exists: {exc}'
                    )

    async with async_session_maker() as check:
        rows = (
            (
                await check.execute(
                    select(OrgBudgetSettings).where(
                        OrgBudgetSettings.org_id == budget_org.id
                    )
                )
            )
            .scalars()
            .all()
        )
        thresholds = (
            (
                await check.execute(
                    select(OrgBudgetThreshold).where(
                        OrgBudgetThreshold.org_id == budget_org.id
                    )
                )
            )
            .scalars()
            .all()
        )

    # A settings row is created only when the org has none.
    assert len(rows) == 1
    # ...and one set of threshold rows, not the winner's stacked on the loser's.
    assert len(thresholds) == len(DEFAULT_THRESHOLDS)


@pytest.mark.asyncio
async def test_race_recovery_hydrates_the_winners_cycle_baselines(
    async_session_maker, budget_org
):
    # The loser returns the winner's settings row, whose user_cycle_start_spend
    # JSON map has not been reconciled against the baseline table. Skipping
    # hydration on this path hands the caller a stale baseline, so the next
    # threshold evaluation compares live spend against the wrong number and the
    # request succeeds with a bad answer.
    cycle_start_at = _current_cycle_start(datetime.now(UTC), 1)
    async with async_session_maker() as winner:
        winner.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=False,
                reset_day=1,
                cycle_start_at=cycle_start_at,
                cycle_start_spend=0.0,
                user_cycle_start_spend={},
            )
        )
        winner.add(
            OrgBudgetCycleBaseline(
                org_id=budget_org.id,
                user_id='member',
                cycle_start_at=cycle_start_at,
                baseline_spend=7.0,
                source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
                observed_at=datetime.now(UTC),
            )
        )
        await winner.commit()

    async with async_session_maker() as loser:
        loser_service = OrgBudgetService(loser)
        with _patch_stale_first_read(loser_service):
            settings = await loser_service._get_or_create_settings(budget_org.id)

    assert settings.user_cycle_start_spend == {'member': 7.0}


@pytest.mark.asyncio
async def test_race_recovery_reraises_when_the_re_read_finds_nothing(
    async_session_maker, budget_org
):
    # The re-read sees the winner's row only under READ COMMITTED. When it comes
    # back empty there is nothing to return, and _get_or_create_settings declares
    # -> OrgBudgetSettings, so returning None would surface several frames away as
    # an AttributeError on NoneType rather than as the unique violation it is.
    async with async_session_maker() as winner:
        winner.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=False,
                reset_day=1,
                cycle_start_at=_current_cycle_start(datetime.now(UTC), 1),
                cycle_start_spend=0.0,
                user_cycle_start_spend={},
            )
        )
        await winner.commit()

    async with async_session_maker() as loser:
        loser_service = OrgBudgetService(loser)
        with patch.object(
            loser_service.store, 'get_settings', AsyncMock(return_value=None)
        ):
            with pytest.raises(IntegrityError) as excinfo:
                await loser_service._get_or_create_settings(budget_org.id)

    assert excinfo.value.orig.sqlstate == _UNIQUE_VIOLATION


@pytest.mark.asyncio
async def test_non_unique_integrity_error_is_not_swallowed_by_the_recovery_read(
    async_session_maker, budget_org
):
    # Only a unique violation means the race was lost; any other IntegrityError
    # must propagate even when the recovery re-read would have found a row. This
    # is what separates the SQLSTATE guard from catching every IntegrityError.
    async with async_session_maker() as winner:
        winner.add(
            OrgBudgetSettings(
                org_id=budget_org.id,
                enabled=False,
                reset_day=1,
                cycle_start_at=_current_cycle_start(datetime.now(UTC), 1),
                cycle_start_spend=0.0,
                user_cycle_start_spend={},
            )
        )
        await winner.commit()

    async with async_session_maker() as loser:
        loser_service = OrgBudgetService(loser)
        not_null_violation = Exception('null value in column violates not-null')
        not_null_violation.sqlstate = '23502'

        with (
            _patch_stale_first_read(loser_service),
            patch.object(
                loser_service.store,
                'create_settings',
                AsyncMock(side_effect=IntegrityError('INSERT', {}, not_null_violation)),
            ),
        ):
            with pytest.raises(IntegrityError) as excinfo:
                await loser_service._get_or_create_settings(budget_org.id)

    assert excinfo.value.orig.sqlstate == '23502'
