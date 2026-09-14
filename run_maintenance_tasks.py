import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from server.logger import logger
from storage.database import session_maker
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskStatus,
)

NUM_RETRIES = 3
RETRY_DELAY = 60


def maintenance_task_status(info: dict) -> MaintenanceTaskStatus:
    """Derive outer task status without discarding processor diagnostics."""
    return (
        MaintenanceTaskStatus.ERROR
        if info.get('error_count', 0) > 0
        else MaintenanceTaskStatus.COMPLETED
    )


async def main():
    set_stale_task_error()
    # Imported lazily so the generic task runner remains usable in tooling
    # that stubs database initialization while importing this module.
    from server.maintenance_task_processor.credential_retirement_processor import (
        enqueue_credential_retirement_tasks,
    )
    from server.maintenance_task_processor.credit_delivery_processor import (
        enqueue_credit_delivery_tasks,
    )
    from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
        enqueue_managed_llm_key_ownership_tasks,
    )

    try:
        enqueue_credential_retirement_tasks()
    except Exception:
        logger.exception('Failed to enqueue credential retirement')

    try:
        enqueued = enqueue_managed_llm_key_ownership_tasks()
        if enqueued:
            logger.info(
                'Enqueued managed LLM key ownership repairs',
                extra={'member_count': enqueued},
            )
    except Exception:
        # One enqueue path must not prevent unrelated pending maintenance
        # tasks from running.
        logger.exception('Failed to enqueue managed LLM key ownership repairs')

    credit_enqueue_failed = False
    try:
        enqueue_credit_delivery_tasks()
    except Exception:
        credit_enqueue_failed = True
        logger.exception('Failed to enqueue pending credit delivery')

    failed_task_count = await run_tasks() + int(credit_enqueue_failed)
    if failed_task_count:
        logger.error(f'{failed_task_count} maintenance task(s) failed')
        raise SystemExit(1)


def expire_stale_tasks(session: Session) -> None:
    # started_at is naive UTC; strip tzinfo before comparing.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    session.query(MaintenanceTask).filter(
        MaintenanceTask.status == MaintenanceTaskStatus.WORKING,
        func.coalesce(
            MaintenanceTask.started_at,
            MaintenanceTask.updated_at,
            MaintenanceTask.created_at,
        )
        < cutoff,
    ).update({MaintenanceTask.status: MaintenanceTaskStatus.ERROR})


def set_stale_task_error():
    with session_maker() as session:
        expire_stale_tasks(session)
        session.commit()


async def run_tasks():
    failed_task_count = 0
    while True:
        with session_maker() as session:
            task = await next_task(session)
            if not task:
                return failed_task_count

            # started_at/updated_at are naive UTC; strip tzinfo.
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            task.status = MaintenanceTaskStatus.WORKING
            task.updated_at = task.started_at = now_utc
            session.commit()

            try:
                processor = task.get_processor()
                task.info = await processor(task)
                task.status = maintenance_task_status(task.info)
                session.commit()
                if task.status == MaintenanceTaskStatus.ERROR:
                    failed_task_count += 1
            except Exception as e:
                task.info = {'error': str(e)}
                task.status = MaintenanceTaskStatus.ERROR
                session.commit()
                failed_task_count += 1

            # wait if there is a delay (this allows us to bypass throttling constraints)
            if task.delay:
                await asyncio.sleep(task.delay)


async def next_task(session) -> MaintenanceTask | None:
    num_retries = NUM_RETRIES
    while True:
        task = (
            session.query(MaintenanceTask)
            .filter(MaintenanceTask.status == MaintenanceTaskStatus.PENDING)
            .order_by(MaintenanceTask.created_at, MaintenanceTask.id)
            # Hold the claim until run_tasks commits WORKING. Other runners skip it.
            .with_for_update(skip_locked=True)
            .first()
        )
        if task:
            return task
        task = next_task
        num_retries -= 1
        if num_retries < 0:
            return None


if __name__ == '__main__':
    asyncio.run(main())
