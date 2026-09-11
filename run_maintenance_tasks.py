import asyncio
from datetime import datetime, timedelta, timezone

from server.logger import logger
from storage.database import session_maker
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskStatus,
)

NUM_RETRIES = 3
RETRY_DELAY = 60


async def main() -> bool:
    """Run stale-task cleanup and pending tasks.

    Returns True when every task completed cleanly, False when any task ended
    in ERROR (processor exception or logical reconciliation failure). The
    CronJob entrypoint translates a False return into a nonzero exit code so
    Kubernetes reports a failed job rather than masking stale enforcement.
    """
    success = True
    try:
        set_stale_task_error()
        success = await run_tasks()
    except Exception:
        logger.exception('Error running maintenance tasks')
        return False
    return success


def set_stale_task_error():
    # started_at is naive UTC; strip tzinfo before comparing.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    with session_maker() as session:
        session.query(MaintenanceTask).filter(
            MaintenanceTask.status == MaintenanceTaskStatus.WORKING,
            MaintenanceTask.started_at < cutoff,
        ).update({MaintenanceTask.status: MaintenanceTaskStatus.ERROR})
        session.commit()


async def run_tasks() -> bool:
    """Process pending maintenance tasks until none remain.

    Returns True when every processed task completed, False when at least one
    task ended in ERROR. A processor may return normally but still signal
    logical failure (e.g. reconciliation drift) via a ``success: False`` flag
    in its result ``info``; such tasks are marked ERROR rather than COMPLETED.
    """
    all_succeeded = True
    while True:
        with session_maker() as session:
            task = await next_task(session)
            if not task:
                return all_succeeded

            # started_at/updated_at are naive UTC; strip tzinfo.
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            task.status = MaintenanceTaskStatus.WORKING
            task.updated_at = task.started_at = now_utc
            session.commit()

            try:
                processor = task.get_processor()
                task.info = await processor(task)
                if isinstance(task.info, dict) and not task.info.get('success', True):
                    # Processor finished without raising but reported logical
                    # failures (e.g. budget reconciliation drift). Surface the
                    # failure so the CronJob exits nonzero.
                    task.status = MaintenanceTaskStatus.ERROR
                    all_succeeded = False
                else:
                    task.status = MaintenanceTaskStatus.COMPLETED
                session.commit()
            except Exception as e:
                task.info = {'error': str(e)}
                task.status = MaintenanceTaskStatus.ERROR
                session.commit()
                all_succeeded = False

            # wait if there is a delay (this allows us to bypass throttling constraints)
            if task.delay:
                await asyncio.sleep(task.delay)


async def next_task(session) -> MaintenanceTask | None:
    num_retries = NUM_RETRIES
    while True:
        task = (
            session.query(MaintenanceTask)
            .filter(MaintenanceTask.status == MaintenanceTaskStatus.PENDING)
            .order_by(MaintenanceTask.created_at)
            .first()
        )
        if task:
            return task
        task = next_task
        num_retries -= 1
        if num_retries < 0:
            return None


if __name__ == '__main__':
    import sys

    sys.exit(0 if asyncio.run(main()) else 1)
