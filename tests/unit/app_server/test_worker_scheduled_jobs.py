"""The CronJob jobs as periodic worker tasks, run by a real worker on this test's Postgres."""

import asyncio
import contextlib
import logging
import time

import psycopg
import pytest
from procrastinate import App, PsycopgConnector

import run_budget_maintenance
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

# Each job's CronJob entrypoint; resend_sync's is sync and must run off the loop.
ENTRYPOINTS = {
    'budget_maintenance': 'run_budget_maintenance.run',
    'maintenance_tasks': 'run_maintenance_tasks.main',
    'app_conversation_start_task_clean': 'sync.clean_app_conversation_start_tasks.main',
    'proactive_convo_clean': 'sync.clean_proactive_convo_table.main',
    'resend_sync': 'sync.resend_keycloak.sync_users_to_resend',
    'install_gitlab_webhooks': 'sync.install_gitlab_webhooks.VerifyWebhookStatus.install_webhooks',
    'enrich_user_interaction': 'sync.enrich_user_interaction_data.main',
}

# A lost deadline would otherwise hang the suite on the planted delay=600 row.
pytestmark = pytest.mark.timeout(60)


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


def test_each_job_keeps_its_cronjobs_schedule_and_overlap_policy():
    blueprint = scheduled_jobs_blueprint(ALL_ON)

    schedules = {task.name: cron for (task, cron) in _periodic(blueprint).items()}
    assert schedules == CHART_SCHEDULES
    for name, task in blueprint.tasks.items():
        assert task.queue == SCHEDULED_JOBS_QUEUE
        assert task.lock == task.queueing_lock == f'{NAMESPACE}:{name}'
        assert task.retry_strategy is None


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


@pytest.mark.usefixtures('app_db_session', 'restore_app')
async def test_worker_skips_scheduled_jobs_queue_when_none_is_enabled(monkeypatch):
    started_with: dict = {}

    async def run_worker_async(**kwargs):
        started_with.update(kwargs)

    monkeypatch.setattr(app, 'run_worker_async', run_worker_async)

    await worker_main.run()

    assert started_with['queues'] == ['default']


def test_budget_cronjob_entrypoint_still_runs_the_job(monkeypatch):
    ran = []

    async def run():
        ran.append(True)

    monkeypatch.setattr(run_budget_maintenance, 'run', run)

    run_budget_maintenance.main()

    assert ran == [True]


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


@contextlib.asynccontextmanager
async def _open_worker_app(conninfo: dict, config: ScheduledJobsConfig):
    """Only ``config``'s jobs, so no other job's periodic run comes due mid-test."""
    worker_app = App(
        connector=PsycopgConnector(kwargs=conninfo, min_size=1, max_size=3)
    )
    worker_app.add_tasks_from(scheduled_jobs_blueprint(config), namespace=NAMESPACE)
    async with worker_app.open_async():
        yield worker_app


def _sql(conninfo: dict, query: str, *params) -> list[tuple]:
    with psycopg.connect(**conninfo, autocommit=True) as connection:
        cursor = connection.execute(query, params)
        return cursor.fetchall() if cursor.description else []


async def _run_job(worker_app: App, conninfo: dict, name: str) -> tuple[str, int]:
    """Run one job to its end on a real worker and return its status and attempts."""
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


@pytest.mark.usefixtures('app_db_session')
async def test_a_job_runs_its_existing_code_in_the_worker(conninfo):
    _sql(
        conninfo,
        'INSERT INTO app_conversation_start_task (id, created_by_user_id, request, created_at) '
        "VALUES (gen_random_uuid(), 'user', '{}', now() - interval '2 days'), "
        "(gen_random_uuid(), 'user', '{}', now())",
    )

    config = ScheduledJobsConfig(app_conversation_start_task_clean=True)
    async with _open_worker_app(conninfo, config) as worker_app:
        status, _ = await _run_job(
            worker_app, conninfo, 'app_conversation_start_task_clean'
        )

    assert status == 'succeeded'
    # Only the row older than a day is gone.
    assert _sql(conninfo, 'SELECT count(*) FROM app_conversation_start_task') == [(1,)]


@pytest.mark.usefixtures('app_db_session')
async def test_a_sync_job_that_exits_fails_and_the_worker_survives(
    conninfo, monkeypatch
):
    from sync import resend_keycloak

    # The job calls sys.exit(1) without its API key, as its CronJob pod did.
    monkeypatch.setattr(resend_keycloak, 'RESEND_API_KEY', None)

    config = ScheduledJobsConfig(resend_sync=True, proactive_convo_clean=True)
    async with _open_worker_app(conninfo, config) as worker_app:
        status, attempts = await _run_job(worker_app, conninfo, 'resend_sync')

        assert (status, attempts) == ('failed', 1)
        # The worker is still usable after the job's SystemExit.
        status, _ = await _run_job(worker_app, conninfo, 'proactive_convo_clean')
        assert status == 'succeeded'


@pytest.mark.usefixtures('app_db_session')
@pytest.mark.parametrize('name', ['budget_maintenance', 'maintenance_tasks'])
async def test_a_budget_job_is_stopped_at_its_deadline(conninfo, monkeypatch, name):
    monkeypatch.setattr(scheduled_jobs, 'DEADLINE_SECONDS', 1)
    # run_maintenance_tasks sleeps for a task's delay after processing it, so this
    # row keeps the job running past its deadline.
    _sql(
        conninfo,
        'INSERT INTO maintenance_tasks (status, processor_type, processor_json, delay) '
        "VALUES ('PENDING', 'not.a.processor', '{}', 600)",
    )

    started = time.monotonic()
    async with _open_worker_app(
        conninfo, ScheduledJobsConfig(**{name: True})
    ) as worker_app:
        status, attempts = await _run_job(worker_app, conninfo, name)

    assert time.monotonic() - started < 30
    assert (status, attempts) == ('failed', 1)


@pytest.mark.usefixtures('app_db_session')
@pytest.mark.parametrize('name', ENTRYPOINTS)
async def test_each_job_runs_its_cronjobs_entrypoint(conninfo, monkeypatch, name):
    # enrich_user_interaction_data builds a GitHub App client at import.
    monkeypatch.setattr(
        'integrations.github.data_collector.GitHubDataCollector', object
    )
    ran_on: list[str] = []

    def record(*args):
        try:
            asyncio.get_running_loop()
            ran_on.append('event loop')
        except RuntimeError:
            ran_on.append('thread')

    async def async_record(*args):
        record()

    entrypoint = record if name == 'resend_sync' else async_record
    monkeypatch.setattr(ENTRYPOINTS[name], entrypoint)

    async with _open_worker_app(
        conninfo, ScheduledJobsConfig(**{name: True})
    ) as worker_app:
        status, _ = await _run_job(worker_app, conninfo, name)

    assert status == 'succeeded'
    # Its own periodic run may come due too, so it can run more than once.
    assert ran_on
    assert set(ran_on) == {'thread' if name == 'resend_sync' else 'event loop'}


async def _wait_for(conninfo: dict, job_id: int, statuses: set[str]) -> str:
    for _ in range(200):
        [(status,)] = _sql(
            conninfo, 'SELECT status FROM procrastinate_jobs WHERE id = %s', job_id
        )
        if status in statuses:
            return status
        await asyncio.sleep(0.1)
    return status


@pytest.mark.usefixtures('app_db_session')
async def test_a_run_that_fails_while_the_next_waits_lets_the_next_start(
    conninfo, monkeypatch
):
    monkeypatch.setattr(scheduled_jobs, 'DEADLINE_SECONDS', 1)
    # The first run claims this row and sleeps past its deadline. The second finds
    # the row claimed, so it has nothing to do and succeeds.
    _sql(
        conninfo,
        'INSERT INTO maintenance_tasks (status, processor_type, processor_json, delay) '
        "VALUES ('PENDING', 'not.a.processor', '{}', 600)",
    )
    config = ScheduledJobsConfig(maintenance_tasks=True)
    async with _open_worker_app(conninfo, config) as worker_app:
        task = worker_app.configure_task(f'{NAMESPACE}:maintenance_tasks')
        first = await task.defer_async(timestamp=0)
        worker = asyncio.create_task(
            worker_app.run_worker_async(queues=[SCHEDULED_JOBS_QUEUE], concurrency=2)
        )
        try:
            assert await _wait_for(conninfo, first, {'doing'}) == 'doing'
            second = await task.defer_async(timestamp=1)

            assert await _wait_for(conninfo, first, {'failed'}) == 'failed'
            assert await _wait_for(conninfo, second, {'succeeded', 'failed'}) == (
                'succeeded'
            )
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
