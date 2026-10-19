"""The Kubernetes CronJobs' jobs, as periodic worker tasks.

For installs without CronJobs, such as Docker Compose. Every job is off until
enabled with ``OH_WORKER_SCHEDULED_JOBS_<JOB>=true``. Never enable a job on a
database where its CronJob still runs: the two schedules would overlap.

Schedules, retries and time limits match the OpenHands-Cloud chart's CronJobs.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from procrastinate import Blueprint, RetryStrategy
from pydantic import BaseModel

from openhands.app_server.worker.housekeeping import SCHEDULED_JOBS_QUEUE

NAMESPACE = 'scheduled_jobs'

# The CronJobs' backoffLimit, and roughly their pod backoff.
RETRY = RetryStrategy(max_attempts=3, wait=10)

# The CronJobs' activeDeadlineSeconds, set only on the two budget jobs.
DEADLINE_SECONDS = 1800


class ScheduledJobsConfig(BaseModel):
    """Which jobs the worker schedules; all off by default."""

    budget_maintenance: bool = False
    maintenance_tasks: bool = False
    app_conversation_start_task_clean: bool = False
    proactive_convo_clean: bool = False
    resend_sync: bool = False
    install_gitlab_webhooks: bool = False
    enrich_user_interaction: bool = False

    def enabled(self) -> list[str]:
        return [name for name, on in self.model_dump().items() if on]


# Each job imports its module when it runs, so the worker loads the job code
# only for the jobs it schedules.


async def budget_maintenance(timestamp: int) -> None:
    import run_budget_maintenance

    async with asyncio.timeout(DEADLINE_SECONDS):
        await run_budget_maintenance.run()


async def maintenance_tasks(timestamp: int) -> None:
    import run_maintenance_tasks

    async with asyncio.timeout(DEADLINE_SECONDS):
        await run_maintenance_tasks.main()


async def app_conversation_start_task_clean(timestamp: int) -> None:
    from sync import clean_app_conversation_start_tasks

    await clean_app_conversation_start_tasks.main()


async def proactive_convo_clean(timestamp: int) -> None:
    from sync import clean_proactive_convo_table

    await clean_proactive_convo_table.main()


def resend_sync(timestamp: int) -> None:
    # Fully synchronous, so procrastinate runs it in a thread.
    from sync import resend_keycloak

    resend_keycloak.sync_users_to_resend()


async def install_gitlab_webhooks(timestamp: int) -> None:
    from sync.install_gitlab_webhooks import VerifyWebhookStatus

    await VerifyWebhookStatus().install_webhooks()


async def enrich_user_interaction(timestamp: int) -> None:
    from sync import enrich_user_interaction_data

    await enrich_user_interaction_data.main()


SCHEDULES: dict[str, tuple[Callable[..., Any], str]] = {
    'budget_maintenance': (budget_maintenance, '*/15 * * * *'),
    'maintenance_tasks': (maintenance_tasks, '0 6 * * *'),
    'app_conversation_start_task_clean': (
        app_conversation_start_task_clean,
        '0 3 * * *',
    ),
    'proactive_convo_clean': (proactive_convo_clean, '0 2 * * *'),
    'resend_sync': (resend_sync, '0 1 * * *'),
    'install_gitlab_webhooks': (install_gitlab_webhooks, '* * * * *'),
    'enrich_user_interaction': (enrich_user_interaction, '*/10 * * * *'),
}


def scheduled_jobs_blueprint(config: ScheduledJobsConfig) -> Blueprint:
    """A periodic task for each enabled job.

    ``lock`` and ``queueing_lock`` allow one run waiting and one running, as the
    CronJobs' ``concurrencyPolicy: Forbid`` does.
    """
    blueprint = Blueprint()
    for name in config.enabled():
        func, cron = SCHEDULES[name]
        lock = f'{NAMESPACE}:{name}'
        task = blueprint.task(
            func,
            name=name,
            queue=SCHEDULED_JOBS_QUEUE,
            lock=lock,
            queueing_lock=lock,
            retry=RETRY,
        )
        blueprint.periodic(cron=cron)(task)
    return blueprint
