from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from run_budget_maintenance import _eligible_budget_org_ids
from server.constants import ORG_SETTINGS_VERSION
from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import (
    OrgBudgetService,
    _current_cycle_start,
)
from sqlalchemy import select
from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.user import User


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
        'migrations.versions.144_remove_personal_org_budget_settings'
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
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
            patch.object(service, '_get_cycle_spend', AsyncMock(return_value=0.0)),
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

        with (
            patch.object(
                service, '_fetch_team_spend', AsyncMock(return_value=42.5)
            ) as fetch_mock,
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            rolled = await service._roll_cycle_if_needed(
                settings, [threshold], overrides
            )

        assert rolled is True
        assert settings.cycle_start_at.replace(tzinfo=UTC) == _current_cycle_start(
            now, reset_day
        )
        assert settings.cycle_start_spend == 42.5
        assert threshold.last_triggered_at is None
        assert threshold.last_triggered_cycle_start is None
        fetch_mock.assert_awaited_once_with(settings.org_id)
        sync_mock.assert_awaited_once_with(settings.org_id, settings, overrides)


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
        with (
            patch.object(service, '_fetch_team_spend', AsyncMock()) as fetch_mock,
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            rolled = await service._roll_cycle_if_needed(settings, [threshold], [])

        assert rolled is False
        assert settings.cycle_start_at == current_cycle_start
        assert threshold.last_triggered_at == now
        fetch_mock.assert_not_called()
        sync_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_budget_maintenance_syncs_when_cycle_not_rolled(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        service = OrgBudgetService(session)
        with patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock:
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
        with (
            patch.object(
                service, '_fetch_team_spend', AsyncMock(return_value=42.5)
            ) as fetch_mock,
            patch.object(service, '_sync_litellm_budgets', AsyncMock()) as sync_mock,
        ):
            result = await service.run_budget_maintenance(budget_org.id)

    assert result['cycle_rolled'] is True
    fetch_mock.assert_awaited_once_with(settings.org_id)
    sync_mock.assert_awaited_once()


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

        financial_data = {
            'members': {
                str(disabled_user_id): {'spend': 12.0},
                str(override_user_id): {'spend': 7.0},
                str(default_user_id): {'spend': 5.0},
            }
        }

        with (
            patch(
                'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(return_value=financial_data),
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

        assert settings.litellm_last_sync_status == 'success'
        assert settings.litellm_last_sync_error is None
        assert settings.litellm_last_sync_at is not None


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
                AsyncMock(return_value={'members': {}}),
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
