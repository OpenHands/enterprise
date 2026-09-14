from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select

from run_budget_maintenance import _eligible_budget_org_ids
from server.constants import ORG_SETTINGS_VERSION
from server.routes.org_models import (
    OrgBudgetSettingsUpdate,
    OrgBudgetThresholdUpdate,
)
from server.services.org_budget_service import (
    BudgetFinancialSnapshotResult,
    LiteLlmFinancialSnapshot,
    LiteLlmMemberFinancialSnapshot,
    OrgBudgetService,
    _budget_policy_comparison,
    _current_cycle_start,
    _next_cycle_start,
)
from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings
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


@pytest.mark.asyncio
async def test_roll_cycle_if_needed_updates_cycle(async_session_maker, budget_org):
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

        with patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock:
            rolled = await service._roll_cycle_if_needed(
                settings, [threshold], overrides, snapshot
            )

        assert rolled is True
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
@pytest.mark.skip(
    reason='reproduces obs:concurrent_roll_reanchored_cycle — fails on current code'
)
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
@pytest.mark.skip(
    reason='reproduces obs:invalid_reset_day_crashed_cycle_math — fails on current code'
)
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


@pytest.mark.asyncio
@pytest.mark.skip(
    reason='reproduces obs:personal_org_settings_created_by_user_row_read '
    '— fails on current code'
)
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
@pytest.mark.skip(
    reason='reproduces obs:override_written_without_litellm_sync — fails on current code'
)
async def test_override_write_reports_failure_when_litellm_is_unreachable(
    async_session_maker, budget_org
):
    # PUT /orgs/{org_id}/budgets/overrides/{user_id} writes the override row first and
    # resyncs LiteLLM after. _sync_litellm_budgets records a 'error' row and returns
    # None rather than raising when the spend read fails, so the endpoint answers 200
    # with the new cap while the proxy still enforces the old one.
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
            # A cap the proxy never received must not be reported as applied.
            with pytest.raises(HTTPException):
                await service.upsert_user_override(
                    budget_org.id, user_id, monthly_limit=50.0, is_disabled=False
                )


@pytest.mark.asyncio
@pytest.mark.skip(
    reason='pins cycle_advances_by_one_reset_period — fails on current code'
)
async def test_maintenance_advances_the_cycle_by_one_reset_period(
    async_session_maker, budget_org
):
    # When maintenance has not run for an org in months -- a paused CronJob, an
    # outage, an org the scheduler only just started covering -- _roll_cycle_if_needed
    # rolls once and sets cycle_start_at to _current_cycle_start(now), skipping every
    # period in between, while cycle_start_spend is re-anchored to today's cumulative
    # total. Everything spent in the months nobody rolled is never counted against any
    # cap.
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
            result = await service.run_budget_maintenance(budget_org.id)

        settings = (
            await session.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == budget_org.id
                )
            )
        ).scalar_one()

    assert result['cycle_rolled'] is True
    # A committed roll moves the cycle start forward by exactly one reset period.
    assert settings.cycle_start_at == _next_cycle_start(stale_cycle_start, reset_day)


@pytest.mark.asyncio
@pytest.mark.skip(
    reason='pins member_cap_never_below_cycle_baseline — fails on current code'
)
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
        snapshot = _snapshot(
            team_spend=baseline, members={str(user_id): (baseline, None, True)}
        )
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

    written_caps = [
        call_args.kwargs['max_budget']
        for call_args in update_user.await_args_list
        if call_args.kwargs.get('max_budget') is not None
    ]

    # A cap below the cycle baseline is already exceeded the moment it is written.
    assert written_caps
    assert min(written_caps) >= baseline


@pytest.mark.asyncio
@pytest.mark.skip(
    reason='pins alert_fires_at_most_once_per_cycle — fails on current code'
)
async def test_threshold_alerts_once_per_cycle_across_a_settings_edit(
    async_session_maker, budget_org
):
    # _maybe_send_alerts dedupes on threshold.last_triggered_cycle_start, but
    # OrgBudgetStore.replace_thresholds deletes every threshold row and inserts fresh
    # ones carrying no latch columns. An admin who edits the thresholds -- here just
    # turning Slack on for the 80% alert -- re-arms every alert inside the live cycle,
    # so the next maintenance run pages the same admins again for spend they have
    # already acknowledged.
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
        ):
            # 85 of a 100 cap crosses the 80% threshold: the admins are paged.
            await service.run_budget_maintenance(budget_org.id)
            assert send_alerts.await_count == 1

            # An ordinary settings edit that only turns Slack on for that threshold.
            await service.update_budget_settings(
                budget_org.id,
                OrgBudgetSettingsUpdate(
                    thresholds=[
                        OrgBudgetThresholdUpdate(
                            percentage=80, email_enabled=True, slack_enabled=True
                        )
                    ]
                ),
            )
            await session.commit()

            # Same cycle, same spend, nothing newly crossed.
            await service.run_budget_maintenance(budget_org.id)

    # Each threshold alerts once per cycle.
    assert send_alerts.await_count == 1
