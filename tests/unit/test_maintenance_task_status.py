from run_maintenance_tasks import maintenance_task_status
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
