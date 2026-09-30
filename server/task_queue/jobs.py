"""The scheduled jobs the task queue runs, formerly Kubernetes CronJobs.

Each job calls its existing ``main()`` unchanged. The module is imported only
when the job runs, because some of them build external clients at import
(enrichment needs GitHub App credentials) and a disabled job must not require
its configuration.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from server.task_queue.config import Role


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    role: Role
    default_schedule: str
    module: str
    # Skip an occurrence when a newer one of the same job is already queued,
    # running or done. True only for scans where a later run covers everything
    # an earlier one would have done.
    skip_superseded: bool

    @property
    def task_name(self) -> str:
        return f'scheduled:{self.name}'

    @property
    def lock(self) -> str:
        """Execution lock: at most one occurrence of the job runs at a time.

        Never a queueing_lock. Procrastinate keeps ``queueing_lock`` when it
        re-queues a retry, and the queueing-lock index is unique over ``todo``
        jobs, so a retry would collide with the next queued occurrence.
        """
        return self.task_name

    @property
    def enabled_env(self) -> str:
        return f'TASK_QUEUE_{self.name.upper()}_ENABLED'

    @property
    def schedule_env(self) -> str:
        return f'TASK_QUEUE_{self.name.upper()}_SCHEDULE'

    def load(self) -> Callable[[], Awaitable[None]]:
        return importlib.import_module(self.module).main


JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        name='clean_app_conversation_start_tasks',
        role=Role.BUSINESS,
        default_schedule='0 3 * * *',
        module='sync.clean_app_conversation_start_tasks',
        skip_superseded=True,
    ),
    ScheduledJob(
        name='clean_proactive_convo_table',
        role=Role.BUSINESS,
        default_schedule='0 2 * * *',
        module='sync.clean_proactive_convo_table',
        skip_superseded=True,
    ),
    ScheduledJob(
        name='enrich_user_interaction_data',
        role=Role.BUSINESS,
        default_schedule='*/10 * * * *',
        module='sync.enrich_user_interaction_data',
        skip_superseded=False,
    ),
)

JOBS_BY_NAME = {job.name: job for job in JOBS}
