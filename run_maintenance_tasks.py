import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy.orm import Session

from server.logger import logger
from storage.database import session_maker
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskStatus,
)

NUM_RETRIES = 3
RETRY_DELAY = 60


class _TaskDiagnostics(BaseModel):
    model_config = ConfigDict(strict=True)
    error_count: int | float = 0


def maintenance_task_status(info: Mapping[str, JsonValue]) -> MaintenanceTaskStatus:
    """Derive outer task status without discarding processor diagnostics."""
    return (
        MaintenanceTaskStatus.ERROR
        if _TaskDiagnostics.model_validate(info).error_count > 0
        else MaintenanceTaskStatus.COMPLETED
    )


async def main() -> None:
    from server.auth.auth_config import ENABLE_KEYCLOAK
    from server.auth.bootstrap import verify_auth_installation

    await verify_auth_installation()
    if not ENABLE_KEYCLOAK:
        from server.services.native_maintenance_service import run_native_maintenance

        result = await run_native_maintenance()
        if result.get('error_count'):
            logger.warning('Native maintenance has pending retries', extra=result)
    # Imported lazily so the generic task runner remains usable in tooling
    # that stubs database initialization while importing this module.
    from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
        enqueue_managed_llm_key_ownership_tasks,
    )

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

    set_stale_task_error()
    failed_task_count = await run_tasks()
    if failed_task_count:
        logger.error(f'{failed_task_count} maintenance task(s) failed')
        raise SystemExit(1)


def set_stale_task_error() -> None:
    # started_at is naive UTC; strip tzinfo before comparing.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    with session_maker() as session:
        session.query(MaintenanceTask).filter(
            MaintenanceTask.status == MaintenanceTaskStatus.WORKING,
            MaintenanceTask.started_at < cutoff,
        ).update({MaintenanceTask.status: MaintenanceTaskStatus.ERROR})
        session.commit()


async def run_tasks() -> int:
    from server.auth.bootstrap import verify_auth_installation

    await verify_auth_installation()
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


async def next_task(session: Session) -> MaintenanceTask | None:
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
        num_retries -= 1
        if num_retries < 0:
            return None


if __name__ == '__main__':
    asyncio.run(main())
