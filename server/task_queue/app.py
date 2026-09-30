"""Build the Procrastinate app for one worker role."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import croniter  # type: ignore[import-untyped]
from procrastinate import App, PsycopgConnector

from server.task_queue.config import Settings
from server.task_queue.jobs import JOBS, ScheduledJob
from server.task_queue.tasks import register_tasks

# Enough occurrences to see the longest gap of any schedule that repeats within
# a year, without walking a year of minutes for a minutely one.
_GAP_SAMPLE_OCCURRENCES = 1000


def longest_gap(schedule: str) -> timedelta:
    """Longest interval between consecutive occurrences of a cron schedule."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = start + timedelta(days=366)
    it = croniter.croniter(schedule, start)
    previous = it.get_next(datetime)
    gap = timedelta(0)
    for _ in range(_GAP_SAMPLE_OCCURRENCES):
        current = it.get_next(datetime)
        gap = max(gap, current - previous)
        if current > horizon:
            break
        previous = current
    return gap


def scheduled_jobs(settings: Settings) -> list[tuple[ScheduledJob, str]]:
    """The jobs this worker schedules, with the cron expression each uses."""
    if not settings.scheduling_enabled:
        return []
    return [
        (job, settings.cron_overrides.get(job.name, job.default_schedule))
        for job in JOBS
        if job.role == settings.role and job.name in settings.enabled_jobs
    ]


def build_app(settings: Settings) -> App:
    app = App(
        connector=PsycopgConnector(
            conninfo=settings.conninfo,
            min_size=1,
            max_size=settings.concurrency + 3,
            timeout=settings.pool_timeout,
        ),
        periodic_defaults={'max_delay': settings.max_delay.total_seconds()},
    )
    register_tasks(app)

    for job, schedule in scheduled_jobs(settings):
        if not croniter.croniter.is_valid(schedule):
            raise ValueError(f'{job.name}: invalid cron schedule {schedule!r}')
        gap = longest_gap(schedule)
        if gap >= settings.max_delay:
            # An occurrence missed at startup would be dropped before any task
            # code runs, and nothing downstream could recover it.
            raise ValueError(
                f'{job.name}: schedule {schedule!r} can leave {gap} between runs, '
                f'which is not covered by max_delay {settings.max_delay}'
            )
        app.periodic_registry.register_task(
            task=app.tasks[job.task_name],
            cron=schedule,
            periodic_id=job.name,
            configure_kwargs={},
        )
    return app
