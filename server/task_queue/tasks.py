"""Procrastinate task definitions for the scheduled jobs.

Every task is an execution-locked Class A scan: the scan is the work, so there
is no durable per-row record and Procrastinate's own retry and stalled recovery
are the recovery authority.
"""

from __future__ import annotations

import enum
import time
from datetime import UTC, datetime

from procrastinate import App, JobContext, RetryStrategy

from openhands.app_server.utils.logger import openhands_logger as logger
from server.task_queue import watermark
from server.task_queue.config import MAX_ATTEMPTS
from server.task_queue.jobs import JOBS, ScheduledJob

RETRY_WAIT_SECONDS = 60

_SUPERSEDED_QUERY = """
SELECT EXISTS (
    SELECT 1 FROM procrastinate_jobs
     WHERE task_name = %(task_name)s
       AND id <> %(job_id)s
       AND status IN ('todo', 'doing', 'succeeded')
       AND (args->>'timestamp')::bigint > %(timestamp)s
) AS superseded
"""


class Outcome(str, enum.Enum):
    """How an occurrence ended. Only EXECUTED counts as a successful run."""

    EXECUTED = 'executed'
    SKIPPED_STALE = 'skipped_stale'
    SKIPPED_WATERMARK = 'skipped_watermark'
    FAILED = 'failed'


def _log_outcome(
    job: ScheduledJob,
    context: JobContext,
    timestamp: int,
    outcome: Outcome,
    started: float,
) -> None:
    logger.info(
        'task_queue.outcome',
        extra={
            'task_queue_job': job.name,
            'job_id': context.job.id,
            'attempt': context.job.attempts,
            'occurrence': datetime.fromtimestamp(timestamp, UTC).isoformat(),
            'lateness_seconds': round(time.time() - timestamp, 3),
            'duration_seconds': round(time.monotonic() - started, 3),
            'outcome': outcome.value,
        },
    )


async def _superseded(context: JobContext, job: ScheduledJob, timestamp: int) -> bool:
    row = await context.app.connector.execute_query_one_async(
        _SUPERSEDED_QUERY,
        task_name=job.task_name,
        job_id=context.job.id,
        timestamp=timestamp,
    )
    return bool(row['superseded'])


async def run_occurrence(
    context: JobContext, job: ScheduledJob, timestamp: int
) -> Outcome:
    started = time.monotonic()
    occurrence = datetime.fromtimestamp(timestamp, UTC)
    connector = context.app.connector
    mark = await watermark.load(connector, job)
    if mark is not None and mark.covers(job, occurrence):
        _log_outcome(job, context, timestamp, Outcome.SKIPPED_WATERMARK, started)
        return Outcome.SKIPPED_WATERMARK
    if job.skip_superseded and await _superseded(context, job, timestamp):
        _log_outcome(job, context, timestamp, Outcome.SKIPPED_STALE, started)
        return Outcome.SKIPPED_STALE
    try:
        await job.load()()
    except Exception:
        _log_outcome(job, context, timestamp, Outcome.FAILED, started)
        raise
    _log_outcome(job, context, timestamp, Outcome.EXECUTED, started)
    try:
        await watermark.advance(connector, job, occurrence)
    except Exception:
        # Advisory: a stale watermark only means this occurrence may run again.
        logger.exception(
            'task_queue.watermark_advance_failed', extra={'task_queue_job': job.name}
        )
    return Outcome.EXECUTED


def _register(app: App, job: ScheduledJob) -> None:
    @app.task(
        name=job.task_name,
        queue=job.role.queue,
        lock=job.lock,
        pass_context=True,
        retry=RetryStrategy(max_attempts=MAX_ATTEMPTS - 1, wait=RETRY_WAIT_SECONDS),
    )
    async def scheduled(context: JobContext, timestamp: int) -> str:
        return (await run_occurrence(context, job, timestamp)).value


def register_tasks(app: App) -> None:
    for job in JOBS:
        _register(app, job)
