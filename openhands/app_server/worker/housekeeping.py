"""Jobs that keep procrastinate's own tables healthy.

Both follow procrastinate's production guides:

- https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html
- https://procrastinate.readthedocs.io/en/stable/howto/production/delete_finished_jobs.html
"""

import logging

from procrastinate import Blueprint, JobContext, builtin_tasks
from procrastinate.exceptions import UniqueViolation

_logger = logging.getLogger(__name__)

# Finished jobs are kept this long, as a record of what the worker did.
KEEP_FINISHED_JOBS_HOURS = 72

housekeeping = Blueprint()


@housekeeping.periodic(cron='*/10 * * * *')
@housekeeping.task(
    name='retry_stalled_jobs',
    queueing_lock='worker:retry_stalled_jobs',
    pass_context=True,
)
async def retry_stalled_jobs(context: JobContext, timestamp: int) -> None:
    """Queue again the jobs of workers that stopped sending heartbeats.

    The worker and ``get_stalled_jobs`` use procrastinate's defaults, which fit
    together: a heartbeat every 10 seconds, and 30 seconds without one counts
    as stalled. A worker taken for stalled can still finish its job, so every
    job must be safe to run twice.
    """
    job_manager = context.app.job_manager
    for job in await job_manager.get_stalled_jobs():
        log_extra = {'job_id': job.id, 'task_name': job.task_name}
        try:
            await job_manager.retry_job(job)
        except UniqueViolation:
            # A newer job with the same queueing lock is still waiting. This
            # one is retried on a later run, once that job has started.
            _logger.info('worker.stalled_job_retry_deferred', extra=log_extra)
        else:
            _logger.info('worker.stalled_job_retried', extra=log_extra)


@housekeeping.periodic(cron='0 4 * * *')
@housekeeping.task(
    name='remove_old_jobs',
    queueing_lock='worker:remove_old_jobs',
    pass_context=True,
)
async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
    """Delete finished jobs, and their events, after three days."""
    await builtin_tasks.remove_old_jobs(
        context,
        max_hours=KEEP_FINISHED_JOBS_HOURS,
        remove_failed=True,
        remove_cancelled=True,
        remove_aborted=True,
    )
