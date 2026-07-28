import asyncio

# Import unqualified: the production Docker image does `COPY enterprise .` into
# WORKDIR /app, flattening the directory so there is no top-level `enterprise`
# package.  `run_maintenance_tasks` lives alongside `server/` and `storage/`
# inside the `enterprise/` source tree and must be imported without a package
# prefix, matching how `maintenance-tasks-cronjob.yaml` already calls
# `python -m run_maintenance_tasks`.
import run_maintenance_tasks
from server.logger import logger
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from storage.database import session_maker
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org_budget_settings import OrgBudgetSettings

BATCH_SIZE = 25


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def enqueue_budget_tasks(batch_size: int = BATCH_SIZE) -> int:
    with session_maker() as session:
        processor_type = (
            f'{OrgBudgetMaintenanceProcessor.__module__}.'
            f'{OrgBudgetMaintenanceProcessor.__name__}'
        )
        existing = (
            session.query(MaintenanceTask)
            .filter(
                MaintenanceTask.status.in_(
                    [MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING]
                )
            )
            .filter(MaintenanceTask.processor_type == processor_type)
            .count()
        )
        if existing:
            logger.info(
                'Budget maintenance tasks already queued',
                extra={'count': existing},
            )
            return 0

        org_ids = [str(row.org_id) for row in session.query(OrgBudgetSettings.org_id)]
        if not org_ids:
            return 0

        for batch in _chunked(org_ids, batch_size):
            processor = OrgBudgetMaintenanceProcessor(org_ids=batch)
            task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING)
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
