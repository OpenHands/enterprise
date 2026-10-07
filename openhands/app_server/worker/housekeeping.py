"""Jobs that keep procrastinate's own tables healthy.

Both follow procrastinate's production guides:

- https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html
- https://procrastinate.readthedocs.io/en/stable/howto/production/delete_finished_jobs.html
"""

import logging

from procrastinate import Blueprint, JobContext, builtin_tasks
from procrastinate.exceptions import ConnectorException, UniqueViolation
from procrastinate.jobs import Job, Status
from procrastinate.manager import JobManager

_logger = logging.getLogger(__name__)

# Finished jobs are kept this long, as a record of what the worker did.
KEEP_FINISHED_JOBS_HOURS = 72

SCHEDULED_JOBS_QUEUE = 'scheduled_jobs'

# What procrastinate_finish_job_v1 raises for a job no longer todo or doing.
_JOB_ALREADY_FINISHED = 'not in "doing" or "todo" status'

housekeeping = Blueprint()


@housekeeping.periodic(cron='*/10 * * * *')
@housekeeping.task(
    name='retry_stalled_jobs',
    queueing_lock='worker:retry_stalled_jobs',
    pass_context=True,
)
async def retry_stalled_jobs(context: JobContext, timestamp: int) -> None:
    """Queue again the jobs of workers that stopped sending heartbeats.

    Scheduled jobs are ended as failed instead; their next tick retries them.

    The worker and ``get_stalled_jobs`` use procrastinate's defaults, which fit
    together: a heartbeat every 10 seconds, and 30 seconds without one counts
    as stalled. A worker taken for stalled can still finish its job, so every
    job must be safe to run twice.
    """
    job_manager = context.app.job_manager
    for job in await job_manager.get_stalled_jobs():
        log_extra = {'job_id': job.id, 'task_name': job.task_name}
        if job.queue == SCHEDULED_JOBS_QUEUE:
            await _end_stalled_scheduled_job(job_manager, job, log_extra)
            continue
        try:
            await job_manager.retry_job(job)
        except UniqueViolation:
            # A newer job with the same queueing lock is still waiting. This
            # one is retried on a later run, once that job has started.
            _logger.info('worker.stalled_job_retry_deferred', extra=log_extra)
        else:
            _logger.info('worker.stalled_job_retried', extra=log_extra)


async def _end_stalled_scheduled_job(
    job_manager: JobManager, job: Job, log_extra: dict
) -> None:
    """End a stalled scheduled job as failed rather than queue it again.

    Retrying sets the row back to todo, which violates the queueing lock's
    unique index while the job's next tick is waiting; the job would then stay
    doing and its lock would block that tick. Failed is in neither index, so
    ending it releases the lock and the next tick is the retry.
    """
    try:
        await job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
    except ConnectorException as exc:
        if _JOB_ALREADY_FINISHED in str(exc.__cause__ or exc):
            # It finished between the scan and now; its outcome stands.
            _logger.info('scheduled_jobs.stalled_already_finished', extra=log_extra)
        else:
            _logger.exception('scheduled_jobs.stalled_end_failed', extra=log_extra)
    else:
        _logger.error('scheduled_jobs.stalled', extra=log_extra)


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
