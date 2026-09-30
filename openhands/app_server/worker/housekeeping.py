"""Jobs that keep procrastinate's own tables healthy."""

import logging

from procrastinate import Blueprint, JobContext, builtin_tasks
from procrastinate.exceptions import UniqueViolation
from procrastinate.jobs import Status

_logger = logging.getLogger(__name__)

# Finished jobs are kept this long, as a record of what the worker did.
KEEP_FINISHED_JOBS_HOURS = 7 * 24

housekeeping = Blueprint()


@housekeeping.periodic(cron='*/5 * * * *')
@housekeeping.task(
    name='retry_stalled_jobs',
    queueing_lock='worker:retry_stalled_jobs',
    pass_context=True,
)
async def retry_stalled_jobs(context: JobContext, timestamp: int) -> None:
    """Queue again the jobs of workers that stopped sending heartbeats.

    procrastinate does not do this by itself. A job can therefore run twice,
    when a worker thought stalled finishes it after all, so every job must be
    safe to run twice.
    """
    job_manager = context.app.job_manager
    for job in await job_manager.get_stalled_jobs():
        try:
            await job_manager.retry_job(job)
        except UniqueViolation:
            # A newer job with the same queueing lock is already waiting, and
            # does the same work. This one is marked failed so that it stops
            # counting as stalled.
            _logger.info(
                'worker.stalled_job_superseded',
                extra={'job_id': job.id, 'task_name': job.task_name},
            )
            await job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
        else:
            _logger.info(
                'worker.stalled_job_retried',
                extra={'job_id': job.id, 'task_name': job.task_name},
            )


@housekeeping.periodic(cron='0 4 * * *')
@housekeeping.task(
    name='remove_old_jobs',
    queueing_lock='worker:remove_old_jobs',
    pass_context=True,
)
async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
    """Delete finished jobs, and their events, after a week."""
    await builtin_tasks.remove_old_jobs(
        context,
        max_hours=KEEP_FINISHED_JOBS_HOURS,
        remove_failed=True,
        remove_cancelled=True,
        remove_aborted=True,
    )
