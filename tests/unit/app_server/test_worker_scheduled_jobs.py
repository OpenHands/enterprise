"""The CronJob jobs as periodic worker tasks, run by a real worker on this test's Postgres."""

import logging
import time

import psycopg
import pytest
from procrastinate import App, PsycopgConnector

from openhands.agent_server.env_parser import from_env
from openhands.app_server.utils.logger import openhands_logger
from openhands.app_server.worker import __main__ as worker_main
from openhands.app_server.worker import scheduled_jobs
from openhands.app_server.worker.app import WorkerConfig, app
from openhands.app_server.worker.housekeeping import SCHEDULED_JOBS_QUEUE
from openhands.app_server.worker.scheduled_jobs import (
    NAMESPACE,
    ScheduledJobsConfig,
    scheduled_jobs_blueprint,
)

# The OpenHands-Cloud chart's CronJob schedules.
CHART_SCHEDULES = {
    'budget_maintenance': '*/15 * * * *',
    'maintenance_tasks': '0 6 * * *',
    'app_conversation_start_task_clean': '0 3 * * *',
    'proactive_convo_clean': '0 2 * * *',
    'resend_sync': '0 1 * * *',
    'install_gitlab_webhooks': '* * * * *',
    'enrich_user_interaction': '*/10 * * * *',
}
ALL_ON = ScheduledJobsConfig(**dict.fromkeys(CHART_SCHEDULES, True))


def test_every_job_is_off_unless_enabled(monkeypatch):
    assert from_env(WorkerConfig, 'OH_WORKER').scheduled_jobs.enabled() == []

    monkeypatch.setenv('OH_WORKER_SCHEDULED_JOBS_RESEND_SYNC', 'true')
    monkeypatch.setenv('OH_WORKER_SCHEDULED_JOBS_BUDGET_MAINTENANCE', '1')
    monkeypatch.setenv('OH_WORKER_SCHEDULED_JOBS_MAINTENANCE_TASKS', 'false')

    config = from_env(WorkerConfig, 'OH_WORKER').scheduled_jobs
    assert config.enabled() == ['budget_maintenance', 'resend_sync']


def test_only_enabled_jobs_are_scheduled():
    blueprint = scheduled_jobs_blueprint(ScheduledJobsConfig(resend_sync=True))

    assert list(blueprint.tasks) == ['resend_sync']
    assert scheduled_jobs_blueprint(ScheduledJobsConfig()).tasks == {}


def test_each_job_keeps_its_cronjobs_schedule_overlap_policy_and_retries():
    blueprint = scheduled_jobs_blueprint(ALL_ON)

    schedules = {task.name: cron for (task, cron) in _periodic(blueprint).items()}
    assert schedules == CHART_SCHEDULES
    for name, task in blueprint.tasks.items():
        assert task.queue == SCHEDULED_JOBS_QUEUE
        assert task.lock == task.queueing_lock == f'{NAMESPACE}:{name}'
        assert task.retry_strategy.max_attempts == 3


def _periodic(blueprint) -> dict:
    return {
        periodic.task: periodic.cron
        for periodic in blueprint.periodic_registry.periodic_tasks.values()
    }


@pytest.fixture
def restore_app():
    """Undo the tasks ``worker_main.run`` adds to the process-wide app."""
    tasks = dict(app.tasks)
    periodic = dict(app.periodic_registry.periodic_tasks)
    yield
    app.tasks.clear()
    app.tasks.update(tasks)
    app.periodic_registry.periodic_tasks.clear()
    app.periodic_registry.periodic_tasks.update(periodic)


@pytest.mark.usefixtures('app_db_session', 'restore_app')
async def test_worker_takes_scheduled_jobs_only_once_one_is_enabled(
    monkeypatch, caplog
):
    started_with: dict = {}

    async def run_worker_async(**kwargs):
        # The real call runs until the process is stopped.
        started_with.update(kwargs)

    monkeypatch.setattr(app, 'run_worker_async', run_worker_async)
    monkeypatch.setattr(openhands_logger, 'propagate', True)
    monkeypatch.setenv('OH_WORKER_SCHEDULED_JOBS_RESEND_SYNC', 'true')

    with caplog.at_level(logging.INFO, logger=openhands_logger.name):
        await worker_main.run()

    assert started_with['queues'] == ['default', SCHEDULED_JOBS_QUEUE]
    assert f'{NAMESPACE}:resend_sync' in app.tasks
    assert not any(
        name.startswith(f'{NAMESPACE}:') and name != f'{NAMESPACE}:resend_sync'
        for name in app.tasks
    )
    [started] = [r for r in caplog.records if r.getMessage() == 'worker.started']
    assert started.scheduled_jobs == ['resend_sync']


@pytest.fixture
def conninfo(test_database) -> dict:
    server = test_database.server
    return {
        'host': server.host,
        'port': server.port,
        'user': server.user,
        'password': server.password,
        'dbname': test_database.name,
    }


@pytest.fixture
async def worker_app(conninfo, app_db_session):
    worker_app = App(
        connector=PsycopgConnector(kwargs=conninfo, min_size=1, max_size=3)
    )
    worker_app.add_tasks_from(scheduled_jobs_blueprint(ALL_ON), namespace=NAMESPACE)
    async with worker_app.open_async():
        yield worker_app


def _sql(conninfo: dict, query: str, *params) -> list[tuple]:
    with psycopg.connect(**conninfo, autocommit=True) as connection:
        cursor = connection.execute(query, params)
        return cursor.fetchall() if cursor.description else []


async def _run_job(worker_app: App, conninfo: dict, name: str) -> tuple[str, int]:
    """Run one job to its end on a real worker; its status and attempts after."""
    job_id = await worker_app.configure_task(f'{NAMESPACE}:{name}').defer_async(
        timestamp=0
    )
    await worker_app.run_worker_async(
        queues=[SCHEDULED_JOBS_QUEUE], wait=False, concurrency=1
    )
    [(status, attempts)] = _sql(
        conninfo,
        'SELECT status, attempts FROM procrastinate_jobs WHERE id = %s',
        job_id,
    )
    return status, attempts


async def test_a_job_runs_its_existing_code_in_the_worker(worker_app, conninfo):
    _sql(
        conninfo,
        'INSERT INTO app_conversation_start_task (id, created_by_user_id, request, created_at) '
        "VALUES (gen_random_uuid(), 'user', '{}', now() - interval '2 days'), "
        "(gen_random_uuid(), 'user', '{}', now())",
    )

    status, _ = await _run_job(
        worker_app, conninfo, 'app_conversation_start_task_clean'
    )

    assert status == 'succeeded'
    # Only the row older than a day is gone.
    assert _sql(conninfo, 'SELECT count(*) FROM app_conversation_start_task') == [(1,)]


async def test_a_sync_job_that_exits_is_retried_and_the_worker_survives(
    worker_app, conninfo, monkeypatch
):
    from sync import resend_keycloak

    # The job calls sys.exit(1) without its API key, as its CronJob pod did.
    monkeypatch.setattr(resend_keycloak, 'RESEND_API_KEY', None)

    status, attempts = await _run_job(worker_app, conninfo, 'resend_sync')

    assert (status, attempts) == ('todo', 1)
    # The worker is still usable after the job's SystemExit.
    status, _ = await _run_job(worker_app, conninfo, 'proactive_convo_clean')
    assert status == 'succeeded'


@pytest.mark.parametrize('name', ['budget_maintenance', 'maintenance_tasks'])
async def test_a_budget_job_is_stopped_at_its_deadline(
    worker_app, conninfo, monkeypatch, name
):
    monkeypatch.setattr(scheduled_jobs, 'DEADLINE_SECONDS', 1)
    # The run waits out a task's delay after processing it.
    _sql(
        conninfo,
        'INSERT INTO maintenance_tasks (status, processor_type, processor_json, delay) '
        "VALUES ('PENDING', 'not.a.processor', '{}', 600)",
    )

    started = time.monotonic()
    status, attempts = await _run_job(worker_app, conninfo, name)

    assert time.monotonic() - started < 30
    assert (status, attempts) == ('todo', 1)
