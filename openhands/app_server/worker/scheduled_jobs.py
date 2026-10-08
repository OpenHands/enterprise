"""The CronJob jobs, scheduled by the worker instead (opt-in, off by default).

For installs without Kubernetes CronJobs, such as Docker Compose. The worker
schedules them only when ``OH_PROCRASTINATE_SCHEDULED_JOBS_ENABLED`` is true,
and then only the jobs enabled one by one with ``OH_JOB_<JOB>__ENABLED``.
Kubernetes installs leave the flag off and keep their CronJobs.

Each job's other ``OH_JOB_<JOB>__<NAME>`` variables reach only that job's child
process, as ``<NAME>``. The reserved names set the job itself: ``ENABLED``,
``SCHEDULE``, ``BACKOFF_LIMIT`` and ``ACTIVE_DEADLINE_SECONDS``.

Never enable this against a database whose CronJobs still run these jobs: the
flag does not stop another scheduler.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace

from procrastinate import App, Blueprint, JobContext
from procrastinate.jobs import Status

from openhands.app_server.worker.child_job import (
    ChildJobError,
    run_child_job,
)
from openhands.app_server.worker.housekeeping import SCHEDULED_JOBS_QUEUE

_logger = logging.getLogger(__name__)

FLAG = 'OH_PROCRASTINATE_SCHEDULED_JOBS_ENABLED'
NAMESPACE = 'scheduled_jobs'
SETTING_PREFIX = 'OH_JOB_'
# A maintenance task's claim outlasts the longer budget deadline by this much.
LEASE_MARGIN_SECONDS = 300
LEASE_VARIABLE = 'MAINTENANCE_TASK_CLAIM_LEASE_SECONDS'
_RESERVED = {'ENABLED', 'SCHEDULE', 'BACKOFF_LIMIT', 'ACTIVE_DEADLINE_SECONDS'}


class ScheduledJobsConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    module: str
    # The chart's defaults; schedules are UTC.
    schedule: str
    deadline_seconds: int
    backoff_limit: int = 3

    @property
    def prefix(self) -> str:
        return f'{SETTING_PREFIX}{self.name.upper()}__'


JOBS = (
    ScheduledJob('budget_maintenance', 'run_budget_maintenance', '*/15 * * * *', 1800),
    ScheduledJob('maintenance_tasks', 'run_maintenance_tasks', '0 6 * * *', 1800),
    ScheduledJob(
        'app_conversation_start_task_clean',
        'sync.clean_app_conversation_start_tasks',
        '0 3 * * *',
        1800,
    ),
    ScheduledJob(
        'proactive_convo_clean', 'sync.clean_proactive_convo_table', '0 2 * * *', 1800
    ),
    ScheduledJob('resend_sync', 'sync.resend_keycloak', '0 1 * * *', 3600),
    ScheduledJob(
        'install_gitlab_webhooks', 'sync.install_gitlab_webhooks', '* * * * *', 300
    ),
    ScheduledJob(
        'enrich_user_interaction',
        'sync.enrich_user_interaction_data',
        '*/10 * * * *',
        600,
    ),
)
# Both claim from the maintenance task table, so they share its lease.
_LEASED = ('budget_maintenance', 'maintenance_tasks')


def parse_bool(name: str, value: str | None) -> bool:
    """``true``/``1`` or ``false``/``0``, any case; unset or empty is false."""
    if value is None or value.strip() == '':
        return False
    normalized = value.strip().lower()
    if normalized in ('true', '1'):
        return True
    if normalized in ('false', '0'):
        return False
    raise ScheduledJobsConfigError(
        f'{name}={value!r} is not a boolean; use true, 1, false or 0.'
    )


def scheduled_jobs_enabled(environ: Mapping[str, str]) -> bool:
    return parse_bool(FLAG, environ.get(FLAG))


@dataclass(frozen=True)
class ConfiguredJob:
    job: ScheduledJob
    # The child's whole environment.
    env: dict[str, str]


def configure(
    environ: Mapping[str, str], jobs: tuple[ScheduledJob, ...] = JOBS
) -> list[ConfiguredJob]:
    """The enabled jobs with their settings; raises on any invalid setting."""
    known = {job.prefix for job in jobs}
    for key in environ:
        if key.startswith(SETTING_PREFIX) and not any(
            key.startswith(prefix) for prefix in known
        ):
            raise ScheduledJobsConfigError(f'{key} does not name a scheduled job.')

    settled = [_settle(job, environ) for job in jobs]
    deadlines = {job.name: job.deadline_seconds for job in settled}
    lease = max(deadlines.get(name, 0) for name in _LEASED) + LEASE_MARGIN_SECONDS
    # No job sees another's settings, nor the flag's.
    base = {
        key: value
        for key, value in environ.items()
        if not key.startswith(SETTING_PREFIX) and key != FLAG
    }

    configured = []
    for job in settled:
        if not parse_bool(f'{job.prefix}ENABLED', environ.get(f'{job.prefix}ENABLED')):
            continue
        env = dict(base)
        for key, value in environ.items():
            if key.startswith(job.prefix) and key[len(job.prefix) :] not in _RESERVED:
                env[key[len(job.prefix) :]] = value
        if job.name in _LEASED:
            env[LEASE_VARIABLE] = str(lease)
        configured.append(ConfiguredJob(job=job, env=env))
    return configured


def _settle(job: ScheduledJob, environ: Mapping[str, str]) -> ScheduledJob:
    schedule = environ.get(f'{job.prefix}SCHEDULE', job.schedule).strip()
    if len(schedule.split()) not in (5, 6):
        raise ScheduledJobsConfigError(
            f'{job.prefix}SCHEDULE={schedule!r} is not a cron expression.'
        )
    return replace(
        job,
        schedule=schedule,
        backoff_limit=_int(environ, f'{job.prefix}BACKOFF_LIMIT', job.backoff_limit, 0),
        deadline_seconds=_int(
            environ, f'{job.prefix}ACTIVE_DEADLINE_SECONDS', job.deadline_seconds, 1
        ),
    )


def _int(environ: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    value = environ.get(name)
    if value is None or value.strip() == '':
        return default
    error = ScheduledJobsConfigError(
        f'{name}={value!r} must be an integer of at least {minimum}.'
    )
    try:
        number = int(value)
    except ValueError:
        raise error from None
    if number < minimum:
        raise error
    return number


def register_scheduled_jobs(app: App, configured: list[ConfiguredJob]) -> None:
    """Add a periodic task for each configured job to ``app``.

    Each task is ``scheduled_jobs:<job>`` on the ``scheduled_jobs`` queue. Its
    queueing lock keeps one run waiting at most, and its lock one running, as
    the CronJobs' ``concurrencyPolicy: Forbid`` does. procrastinate itself never
    retries these jobs: the task retries the child, and a stalled job is ended
    as failed by housekeeping, so its next run is the retry.
    """
    blueprint = Blueprint()
    for item in configured:
        lock = f'{NAMESPACE}:{item.job.name}'
        task = blueprint.task(
            _runner(item),
            name=item.job.name,
            queue=SCHEDULED_JOBS_QUEUE,
            lock=lock,
            queueing_lock=lock,
            pass_context=True,
        )
        blueprint.periodic(cron=item.job.schedule)(task)
    app.add_tasks_from(blueprint, namespace=NAMESPACE)


def _runner(item: ConfiguredJob):
    job = item.job

    async def run(context: JobContext, timestamp: int) -> None:
        job_id = context.job.id
        assert job_id is not None

        async def still_current() -> bool:
            # Housekeeping may have ended this job as failed while it stalled.
            status = await context.app.job_manager.get_job_status_async(job_id)
            return status == Status.DOING

        extra = {'job': job.name, 'job_id': job_id}
        try:
            result = await run_child_job(
                job.module,
                env=item.env,
                deadline_seconds=job.deadline_seconds,
                backoff_limit=job.backoff_limit,
                still_current=still_current,
            )
        except ChildJobError as exc:
            _logger.error(
                'scheduled_jobs.failed',
                extra={
                    **extra,
                    'attempts': exc.attempts,
                    'exit_code': exc.exit_code,
                    'reason': type(exc).__name__,
                },
            )
            raise
        if result.superseded:
            _logger.warning(
                'scheduled_jobs.superseded',
                extra={**extra, 'attempts': result.attempts},
            )
        else:
            _logger.info(
                'scheduled_jobs.succeeded', extra={**extra, 'attempts': result.attempts}
            )

    run.__name__ = run.__qualname__ = f'run_{job.name}'
    run.__module__ = __name__
    return run
