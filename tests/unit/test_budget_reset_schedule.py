import asyncio
from datetime import UTC, datetime
from importlib import import_module
from unittest.mock import AsyncMock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from freezegun import freeze_time
from sqlalchemy import select, text

from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import (
    BudgetFinancialSnapshotResult,
    LiteLlmFinancialSnapshot,
    OrgBudgetService,
)
from storage.org_budget_settings import OrgBudgetSettings


def test_upgrade_preserves_existing_budget_schedule(engine, create_org):
    org = create_org()
    migration = import_module('migrations.versions.165_add_budget_next_reset')
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            connection.execute(
                text("""
                INSERT INTO org_budget_settings
                    (org_id, enabled, reset_day, monthly_limit, cycle_start_at,
                     cycle_start_spend, user_cycle_start_spend, created_at, updated_at)
                VALUES (:org_id, true, 15, 100, '2026-08-15T00:00:00Z',
                        40, json_build_object('member', 4), now(), now())
            """),
                {'org_id': org.id},
            )
            migration.upgrade()
        row = (
            connection.execute(
                text('SELECT * FROM org_budget_settings WHERE org_id = :org_id'),
                {'org_id': org.id},
            )
            .mappings()
            .one()
        )
        assert row['next_reset_at'] is None
        assert row['reset_day'] == 15
        assert row['cycle_start_spend'] == 40
        assert row['user_cycle_start_spend'] == {'member': 4}


@pytest.mark.asyncio
@freeze_time('2026-09-10', real_asyncio=True)
async def test_editor_waits_for_maintenance_before_scheduling(
    async_session_maker, create_org
):
    org = create_org()
    async with async_session_maker() as setup:
        setup.add(
            OrgBudgetSettings(
                org_id=org.id,
                enabled=True,
                monthly_limit=100,
                reset_day=1,
                cycle_start_at=datetime(2026, 8, 1, tzinfo=UTC),
                cycle_start_spend=10,
            )
        )
        await setup.commit()
    async with async_session_maker() as worker, async_session_maker() as editor:
        service = OrgBudgetService(worker)
        editor_service = OrgBudgetService(editor)
        entered = asyncio.Event()
        release = asyncio.Event()
        snapshot = LiteLlmFinancialSnapshot(30, 110, {}, datetime.now(UTC))

        async def read_spend(*args, **kwargs):
            entered.set()
            await release.wait()
            return BudgetFinancialSnapshotResult(snapshot, 'live')

        async def maintenance():
            result = await service.run_budget_maintenance(org.id)
            await worker.commit()
            return result

        async def edit():
            result = await editor_service.update_budget_settings(
                org.id, OrgBudgetSettingsUpdate(reset_day=15)
            )
            await editor.commit()
            return result

        with (
            patch.object(
                service, '_get_financial_snapshot', AsyncMock(side_effect=read_spend)
            ),
            patch.object(
                service, '_sync_litellm_budgets', AsyncMock(return_value=snapshot)
            ),
            patch.object(
                editor_service,
                '_sync_litellm_budgets',
                AsyncMock(return_value=snapshot),
            ),
        ):
            worker_task = asyncio.create_task(maintenance())
            await asyncio.wait_for(entered.wait(), 5)
            editor_task = asyncio.create_task(edit())
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(editor_task), 0.1)
            finally:
                release.set()
            rolled, edited = await asyncio.wait_for(
                asyncio.gather(worker_task, editor_task), 5
            )
        assert rolled['cycle_rolled']
        assert edited['settings'].cycle_start_spend == 30
        assert edited['cycle'].start_at == datetime(2026, 9, 1, tzinfo=UTC)
        assert edited['cycle'].end_at == datetime(2026, 9, 15, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'now,new_day,expected',
    [
        ('2026-09-10', 15, '2026-09-15'),
        ('2026-09-10', 1, '2026-10-01'),
        ('2026-09-15', 15, '2026-10-15'),
        ('2026-12-31', 1, '2027-01-01'),
        ('2028-02-29', 15, '2028-03-15'),
    ],
)
async def test_schedule_survives_saves_and_renews_exactly_once(
    async_session_maker, create_org, now, new_day, expected
):
    org = create_org()
    boundary = datetime.fromisoformat(expected).replace(tzinfo=UTC)
    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=org.id,
            enabled=True,
            monthly_limit=100,
            reset_day=1 if new_day == 15 else 15,
            cycle_start_at=datetime(2026, 8, 15, tzinfo=UTC),
            cycle_start_spend=10,
        )
        session.add(settings)
        await session.commit()
        service = OrgBudgetService(session)
        snapshot = LiteLlmFinancialSnapshot(30, 110, {}, boundary)
        with (
            patch.object(
                service, '_sync_litellm_budgets', AsyncMock(return_value=snapshot)
            ),
            patch.object(
                service,
                '_get_financial_snapshot',
                AsyncMock(return_value=BudgetFinancialSnapshotResult(snapshot, 'live')),
            ),
        ):
            with freeze_time(now):
                result = await service.update_budget_settings(
                    org.id, OrgBudgetSettingsUpdate(reset_day=new_day)
                )
                await session.commit()
                assert result['cycle'].end_at == boundary
                assert result['current_spend'] == 20

            with freeze_time(boundary):
                # A form save on/after the deadline must not postpone the reset.
                result = await service.update_budget_settings(
                    org.id,
                    OrgBudgetSettingsUpdate(reset_day=new_day, monthly_limit=120),
                )
                await session.commit()
                assert result['cycle'].end_at == boundary
                assert result['current_spend'] == 20
                assert (await service.run_budget_maintenance(org.id))['cycle_rolled']
                await session.commit()
                assert settings.next_reset_at is None
                assert settings.cycle_start_at == boundary
                assert settings.cycle_start_spend == 30
                assert not (await service.run_budget_maintenance(org.id))[
                    'cycle_rolled'
                ]
                assert (await service.get_budget_state(org.id))['current_spend'] == 0


@pytest.mark.asyncio
@freeze_time('2026-09-10')
async def test_worker_with_cached_settings_observes_saved_reset_schedule(
    async_session_maker, create_org
):
    org = create_org()
    async with async_session_maker() as setup:
        setup.add(
            OrgBudgetSettings(
                org_id=org.id,
                enabled=True,
                monthly_limit=100,
                reset_day=15,
                cycle_start_at=datetime(2026, 8, 15, tzinfo=UTC),
                cycle_start_spend=10,
            )
        )
        await setup.commit()
    async with async_session_maker() as editor, async_session_maker() as worker:
        worker_service = OrgBudgetService(worker)
        cached = await worker_service.store.get_settings(org.id)
        editor_service = OrgBudgetService(editor)
        snapshot = LiteLlmFinancialSnapshot(30, 110, {}, datetime.now(UTC))
        with patch.object(
            editor_service, '_sync_litellm_budgets', AsyncMock(return_value=snapshot)
        ):
            await editor_service.update_budget_settings(
                org.id, OrgBudgetSettingsUpdate(reset_day=1)
            )
        await editor.commit()
        with (
            patch.object(
                worker_service,
                '_sync_litellm_budgets',
                AsyncMock(return_value=snapshot),
            ),
            patch.object(
                worker_service,
                '_get_financial_snapshot',
                AsyncMock(return_value=BudgetFinancialSnapshotResult(snapshot, 'live')),
            ),
        ):
            result = await worker_service.run_budget_maintenance(org.id)
        await worker.commit()
        assert not result['cycle_rolled']
        assert result['cycle_end_at'] == datetime(2026, 10, 1, tzinfo=UTC)
        assert result['current_spend'] == 20
        assert cached.reset_day == 1
        assert cached.cycle_start_spend == 10
        assert (
            await worker.scalar(
                select(OrgBudgetSettings.next_reset_at).where(
                    OrgBudgetSettings.org_id == org.id
                )
            )
            == result['cycle_end_at']
        )
