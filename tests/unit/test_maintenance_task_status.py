from unittest.mock import AsyncMock, patch

import pytest

from run_maintenance_tasks import main, maintenance_task_status, run_tasks
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus


@pytest.fixture(autouse=True)
def isolated_enqueue_paths():
    with (
        patch(
            'server.maintenance_task_processor.credential_retirement_processor.enqueue_credential_retirement_tasks',
            return_value=0,
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.enqueue_managed_llm_key_ownership_tasks',
            return_value=0,
        ),
        patch(
            'server.maintenance_task_processor.credit_delivery_processor.enqueue_credit_delivery_tasks',
            return_value=0,
        ),
    ):
        yield


def test_structured_processor_failure_marks_outer_task_error():
    info = {
        'processed': 1,
        'error_count': 1,
        'errors': [{'org_id': 'org-1', 'error': 'reconciliation failed'}],
    }

    assert maintenance_task_status(info) == MaintenanceTaskStatus.ERROR


def test_zero_processor_errors_marks_outer_task_completed():
    assert maintenance_task_status({'error_count': 0}) == (
        MaintenanceTaskStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_main_exits_nonzero_after_task_failures():
    with (
        patch('run_maintenance_tasks.set_stale_task_error'),
        patch(
            'run_maintenance_tasks.run_tasks',
            new=AsyncMock(return_value=1),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        await main()

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_credit_enqueue_failure_is_visible_and_does_not_skip_other_tasks():
    with (
        patch('run_maintenance_tasks.set_stale_task_error'),
        patch(
            'server.maintenance_task_processor.credit_delivery_processor.enqueue_credit_delivery_tasks',
            side_effect=RuntimeError('database unavailable'),
        ),
        patch('run_maintenance_tasks.run_tasks', new=AsyncMock(return_value=0)) as run,
        pytest.raises(SystemExit) as caught,
    ):
        await main()
    assert caught.value.code == 1
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_returns_normally_after_successful_tasks():
    with (
        patch('run_maintenance_tasks.set_stale_task_error'),
        patch(
            'run_maintenance_tasks.run_tasks',
            new=AsyncMock(return_value=0),
        ),
    ):
        await main()


@pytest.mark.asyncio
async def test_run_tasks_returns_failed_count_for_structured_processor_failures(
    session_maker,
):
    processor = AsyncMock(
        return_value={
            'processed': 1,
            'error_count': 1,
            'errors': [{'org_id': 'org-1', 'error': 'reconciliation failed'}],
        }
    )

    with session_maker() as session:
        task = MaintenanceTask(
            status=MaintenanceTaskStatus.PENDING,
            processor_type='test.processor',
            processor_json='{}',
        )
        session.add(task)
        session.commit()
        task_id = task.id

    with (
        patch(
            'storage.maintenance_task.MaintenanceTask.get_processor',
            return_value=processor,
        ),
        patch('run_maintenance_tasks.session_maker', session_maker),
    ):
        failed_task_count = await run_tasks()

    assert failed_task_count == 1
    with session_maker() as session:
        updated = session.get(MaintenanceTask, task_id)
    assert updated is not None
    assert updated.status == MaintenanceTaskStatus.ERROR
