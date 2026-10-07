import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, update

from server.logger import logger
from storage.database import session_maker
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskStatus,
)

# Both budget CronJobs claim from this table, so the lease outlasts the larger
# of their activeDeadlineSeconds (1800 s by default) by 5 min: a run's previous
# owner has been stopped by its deadline before the task can be reclaimed. The
# chart passes the value derived from the configured deadlines.
DEFAULT_CLAIM_LEASE_SECONDS = 2100


def maintenance_task_status(info: dict) -> MaintenanceTaskStatus:
    """Derive outer task status without discarding processor diagnostics."""
    return (
        MaintenanceTaskStatus.ERROR
        if info.get('error_count', 0) > 0
        else MaintenanceTaskStatus.COMPLETED
    )


async def main():
    # A headless CronJob must never launch LiteLLM's interactive device login
    # (chatgpt/github_copilot). Otherwise validating an org whose agent settings
    # reference such a model blocks the run for ~15 min per model and wedges the
    # whole schedule (OpenHands/enterprise#565).
    from server.utils.litellm_interactive_login_guard import (
        install_litellm_interactive_login_guard,
    )

    install_litellm_interactive_login_guard()

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

    failed_task_count = await run_tasks()
    if failed_task_count:
        logger.error(f'{failed_task_count} maintenance task(s) failed')
        raise SystemExit(1)


def claim_lease() -> timedelta:
    return timedelta(
        seconds=int(
            os.getenv(
                'MAINTENANCE_TASK_CLAIM_LEASE_SECONDS',
                str(DEFAULT_CLAIM_LEASE_SECONDS),
            )
        )
    )


def _naive_utc_now() -> datetime:
    # started_at and updated_at are naive UTC columns.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def claim_next_task(run_id: UUID, lease: timedelta) -> MaintenanceTask | None:
    """Claim the oldest task that is pending, or whose claim's lease has run out.

    Rows written by code without claims have no claimed_at; their started_at
    (naive UTC) stands in for it. The returned task is detached.
    """
    claim_started = func.coalesce(
        MaintenanceTask.claimed_at,
        func.timezone('UTC', MaintenanceTask.started_at),
    )
    with session_maker(expire_on_commit=False) as session:
        task = (
            session.query(MaintenanceTask)
            .filter(
                or_(
                    MaintenanceTask.status == MaintenanceTaskStatus.PENDING,
                    and_(
                        MaintenanceTask.status == MaintenanceTaskStatus.WORKING,
                        claim_started < func.now() - lease,
                    ),
                )
            )
            .order_by(MaintenanceTask.created_at, MaintenanceTask.id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if task is None:
            return None
        if task.status == MaintenanceTaskStatus.WORKING:
            logger.warning(
                'maintenance_task.reclaimed',
                extra={
                    'task_id': task.id,
                    'previous_claim_run_id': str(task.claim_run_id),
                    'claim_run_id': str(run_id),
                },
            )
        task.status = MaintenanceTaskStatus.WORKING
        task.claim_run_id = run_id
        task.claimed_at = func.now()
        task.started_at = task.updated_at = _naive_utc_now()
        session.flush()
        session.refresh(task)
        session.commit()
        return task


def finish_task(
    task_id: int,
    run_id: UUID,
    info: dict[str, Any],
    status: MaintenanceTaskStatus,
) -> bool:
    """Record the outcome, unless another run has taken the task over since."""
    with session_maker() as session:
        result = session.execute(
            update(MaintenanceTask)
            .where(
                MaintenanceTask.id == task_id,
                MaintenanceTask.claim_run_id == run_id,
            )
            .values(info=info, status=status, updated_at=_naive_utc_now())
        )
        session.commit()
    if result.rowcount == 0:
        logger.warning(
            'maintenance_task.stale_write_rejected',
            extra={'task_id': task_id, 'claim_run_id': str(run_id)},
        )
        return False
    return True


async def run_tasks() -> int:
    run_id = uuid4()
    lease = claim_lease()
    failed_task_count = 0
    while True:
        task = claim_next_task(run_id, lease)
        if task is None:
            return failed_task_count

        info: dict[str, Any]
        try:
            processor = task.get_processor()
            info = await processor(task)
            status = maintenance_task_status(info)
        except Exception as e:
            info = {'error': str(e)}
            status = MaintenanceTaskStatus.ERROR

        if (
            finish_task(task.id, run_id, info, status)
            and status == MaintenanceTaskStatus.ERROR
        ):
            failed_task_count += 1

        # wait if there is a delay (this allows us to bypass throttling constraints)
        if task.delay:
            await asyncio.sleep(task.delay)


if __name__ == '__main__':
    asyncio.run(main())
