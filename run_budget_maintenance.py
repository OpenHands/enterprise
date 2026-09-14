import asyncio

from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select, text

# `run_maintenance_tasks` is a top-level module beside this file at the repository
# root (/app in the Docker image); `maintenance-tasks-cronjob.yaml` runs it as
# `python -m run_maintenance_tasks`.
import run_maintenance_tasks
from server.logger import logger
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from storage.database import session_maker
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.user import User

BATCH_SIZE = 25
ENQUEUE_LOCK = 0x4F48425544514555


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _eligible_budget_org_ids(session) -> list[str]:
    return [
        str(row.org_id)
        for row in session.query(OrgBudgetSettings.org_id)
        .outerjoin(User, User.id == OrgBudgetSettings.org_id)
        .filter(
            User.id.is_(None),
            or_(
                and_(
                    OrgBudgetSettings.enabled.is_(True),
                    OrgBudgetSettings.control_mode == 'managed',
                ),
                exists(
                    select(OrgBudgetOperation.id).where(
                        OrgBudgetOperation.org_id == OrgBudgetSettings.org_id,
                        OrgBudgetOperation.status == 'pending',
                    )
                ),
            ),
        )
        .order_by(OrgBudgetSettings.org_id)
    ]


def enqueue_budget_tasks(batch_size: int = BATCH_SIZE) -> int:
    if batch_size < 1:
        raise ValueError('Budget maintenance batch size must be positive')
    with session_maker() as session:
        # Queue deduplication is atomic; per-org execution still uses the budget lock.
        session.execute(
            text('SELECT pg_advisory_xact_lock(:key)'), {'key': ENQUEUE_LOCK}
        )
        run_maintenance_tasks.expire_stale_tasks(session)
        processor_type = (
            f'{OrgBudgetMaintenanceProcessor.__module__}.'
            f'{OrgBudgetMaintenanceProcessor.__name__}'
        )
        active_tasks = (
            session.query(MaintenanceTask)
            .filter(
                MaintenanceTask.status.in_(
                    [MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING]
                )
            )
            .filter(MaintenanceTask.processor_type == processor_type)
            .all()
        )
        queued_orgs: set[str] = set()
        for task in active_tasks:
            try:
                queued_orgs.update(
                    OrgBudgetMaintenanceProcessor.model_validate_json(
                        task.processor_json
                    ).org_ids
                )
            except ValidationError:
                logger.warning(
                    'Invalid budget task does not reserve organizations',
                    extra={'task_id': task.id},
                )

        org_ids = [
            org_id
            for org_id in _eligible_budget_org_ids(session)
            if org_id not in queued_orgs
        ]

        for batch in _chunked(org_ids, batch_size):
            processor = OrgBudgetMaintenanceProcessor(org_ids=batch)
            task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
            task.set_processor(processor)
            session.add(task)

        session.commit()
        return len(org_ids)


def main() -> None:
    total = enqueue_budget_tasks()
    if total:
        logger.info('Enqueued org budget maintenance tasks', extra={'orgs': total})
    else:
        logger.info('No org budget settings found; skipping maintenance enqueue')

    asyncio.run(run_maintenance_tasks.main())


if __name__ == '__main__':
    main()
