"""Exercise production runner recovery, not a test-local imitation of the runner."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from run_budget_maintenance import enqueue_budget_tasks
from run_maintenance_tasks import expire_stale_tasks, main, run_tasks
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org_budget_settings import OrgBudgetSettings


@pytest.mark.asyncio
async def test_main_expires_stale_tasks_before_enqueuing_key_repairs():
    calls = []

    async def run():
        calls.append('run')
        return 0

    with (
        patch(
            'run_maintenance_tasks.set_stale_task_error',
            side_effect=lambda: calls.append('expire'),
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.enqueue_managed_llm_key_ownership_tasks',
            side_effect=lambda: calls.append('enqueue') or 0,
        ),
        patch('run_maintenance_tasks.run_tasks', new=run),
    ):
        await main()
    assert calls == ['expire', 'enqueue', 'run']


def test_stale_cleanup_participates_in_callers_transaction(session_maker):
    with session_maker() as session:
        task = MaintenanceTask(
            status=MaintenanceTaskStatus.WORKING,
            processor_type='test',
            processor_json='{}',
            started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
        )
        session.add(task)
        session.commit()
        task_id = task.id
        expire_stale_tasks(session)
        assert (
            session.get(MaintenanceTask, task_id).status == MaintenanceTaskStatus.ERROR
        )
        session.rollback()
    with session_maker() as session:
        assert (
            session.get(MaintenanceTask, task_id).status
            == MaintenanceTaskStatus.WORKING
        )


@pytest.mark.asyncio
async def test_cancelled_budget_worker_is_requeued_after_stale_timeout(
    session_maker, create_org
):
    org_id = create_org().id
    with session_maker() as session:
        session.add(
            OrgBudgetSettings(org_id=org_id, control_mode='managed', enabled=True)
        )
        task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
        task.set_processor(OrgBudgetMaintenanceProcessor(org_ids=[str(org_id)]))
        session.add(task)
        session.commit()
        task_id = task.id

    reached_processor = asyncio.Event()

    async def interrupted_processor(task):
        reached_processor.set()
        await asyncio.Event().wait()

    with (
        patch('run_maintenance_tasks.session_maker', session_maker),
        patch('run_budget_maintenance.session_maker', session_maker),
        patch.object(
            MaintenanceTask, 'get_processor', return_value=interrupted_processor
        ),
    ):
        worker = asyncio.create_task(run_tasks())
        await asyncio.wait_for(reached_processor.wait(), timeout=5)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

        with session_maker() as session:
            interrupted = session.get(MaintenanceTask, task_id)
            assert interrupted.status == MaintenanceTaskStatus.WORKING
            assert interrupted.started_at is not None
            interrupted.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                hours=2
            )
            session.commit()

        assert enqueue_budget_tasks() == 1
        with session_maker() as session:
            assert (
                session.get(MaintenanceTask, task_id).status
                == MaintenanceTaskStatus.ERROR
            )
            retry = (
                session.query(MaintenanceTask)
                .filter(MaintenanceTask.status == MaintenanceTaskStatus.PENDING)
                .one()
            )
            retry_id = retry.id
            assert retry_id != task_id

        with patch.object(
            MaintenanceTask,
            'get_processor',
            return_value=AsyncMock(return_value={'processed': 1, 'error_count': 0}),
        ):
            assert await run_tasks() == 0

    with session_maker() as session:
        assert (
            session.get(MaintenanceTask, retry_id).status
            == MaintenanceTaskStatus.COMPLETED
        )
        settings = session.query(OrgBudgetSettings).filter_by(org_id=org_id).one()
        assert settings.control_mode == 'managed'
        assert settings.control_generation == 0
