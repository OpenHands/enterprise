"""Remove the worklogs of factories that no longer exist.

The automation service tells Enterprise to purge a factory's worklogs when it
deletes the factory. That call can be missed, so this task asks the automation
service which of the factories that still hold worklogs exist, and removes the
rest. It is not license-gated: cleanup must work on any install.

It never purges on a doubt: when the automation service is not configured or a
lookup fails, the worklogs stay and the failure is counted.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from server.auth.constants import AUTOMATION_SERVICE_URL
from server.logger import logger
from server.services.worklog_service import (
    FactoryLookupError,
    automations_service_key,
    existing_factory_ids,
)
from storage.database import session_maker
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskProcessor,
    MaintenanceTaskStatus,
)
from storage.worklog import Worklog
from storage.worklog_store import WorklogStore

FACTORY_BATCH_SIZE = 500


class WorklogOrphanPurgeProcessor(MaintenanceTaskProcessor):
    async def __call__(self, task: MaintenanceTask) -> dict:
        del task
        if not AUTOMATION_SERVICE_URL or not automations_service_key():
            logger.info('worklog_orphan_purge_skipped_automation_service_unconfigured')
            return {'skipped': 'automation service not configured', 'error_count': 0}

        store = WorklogStore()
        checked = purged = error_count = 0
        after: tuple[UUID, UUID] | None = None
        while True:
            pairs = await store.factories_with_worklogs(after, FACTORY_BATCH_SIZE)
            if not pairs:
                break
            after = pairs[-1]
            by_org: dict[UUID, list[UUID]] = defaultdict(list)
            for org_id, factory_id in pairs:
                by_org[org_id].append(factory_id)
            for org_id, factory_ids in by_org.items():
                checked += len(factory_ids)
                try:
                    known = await existing_factory_ids(org_id, factory_ids)
                except FactoryLookupError:
                    error_count += 1
                    continue
                for factory_id in factory_ids:
                    if factory_id not in known:
                        await store.purge_factory(org_id, factory_id)
                        purged += 1
        logger.info(
            'worklog_orphan_purge_done',
            extra={'checked': checked, 'purged': purged, 'error_count': error_count},
        )
        return {'checked': checked, 'purged': purged, 'error_count': error_count}


def enqueue_worklog_orphan_purge_task() -> bool:
    """Queue one purge task when worklogs exist and none is already queued."""
    processor_type = (
        f'{WorklogOrphanPurgeProcessor.__module__}.'
        f'{WorklogOrphanPurgeProcessor.__name__}'
    )
    with session_maker() as session:
        if session.query(Worklog.id).limit(1).first() is None:
            return False
        queued = (
            session.query(MaintenanceTask)
            .filter(
                MaintenanceTask.status.in_(
                    [MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING]
                ),
                MaintenanceTask.processor_type == processor_type,
            )
            .count()
        )
        if queued:
            return False
        task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
        task.set_processor(WorklogOrphanPurgeProcessor())
        session.add(task)
        session.commit()
        return True
