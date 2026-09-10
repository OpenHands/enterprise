from unittest.mock import AsyncMock, patch

import pytest

from run_maintenance_tasks import main, maintenance_task_status
from storage.maintenance_task import MaintenanceTaskStatus


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
async def test_main_returns_normally_after_successful_tasks():
    with (
        patch('run_maintenance_tasks.set_stale_task_error'),
        patch(
            'run_maintenance_tasks.run_tasks',
            new=AsyncMock(return_value=0),
        ),
    ):
        await main()
