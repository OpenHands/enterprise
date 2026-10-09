"""The worker schedules the CronJob jobs only when told to (worker/scheduled_jobs.py)."""

import asyncio
import logging
import os
import signal
import textwrap
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.jobs import Status
from procrastinate.testing import InMemoryConnector

from openhands.app_server.utils.logger import openhands_logger
from openhands.app_server.worker import __main__ as worker_main
from openhands.app_server.worker import scheduled_jobs as scheduled_jobs_module
from openhands.app_server.worker.scheduled_jobs import (
    FLAG,
    JOBS,
    LEASE_VARIABLE,
    ScheduledJob,
    ScheduledJobsConfigError,
    configure,
    parse_bool,
    register_scheduled_jobs,
    scheduled_jobs_enabled,
)


@pytest.mark.parametrize(
    'value, expected',
    [
        (None, False),
        ('', False),
        ('false', False),
        ('0', False),
        ('FALSE', False),
        ('true', True),
        ('1', True),
        ('True', True),
        (' true ', True),
    ],
)
def test_the_flag_accepts_true_1_false_0_and_defaults_off(value, expected):
    environ = {} if value is None else {FLAG: value}
    assert scheduled_jobs_enabled(environ) is expected


@pytest.mark.parametrize('value', ['yes', 'on', '2', 'enabled'])
def test_any_other_flag_value_is_a_configuration_error(value):
    with pytest.raises(ScheduledJobsConfigError, match=FLAG):
        parse_bool(FLAG, value)


def test_no_job_runs_unless_enabled_one_by_one():
    assert configure({FLAG: 'true'}) == []


def test_an_enabled_job_keeps_the_charts_schedule_deadline_and_backoff():
    [item] = configure({'OH_JOB_MAINTENANCE_TASKS__ENABLED': 'true'})

    assert item.job == ScheduledJob(
        'maintenance_tasks', 'run_maintenance_tasks', '0 6 * * *', 1800, 3
    )


def test_schedule_deadline_and_backoff_can_be_set_per_job():
    [item] = configure(
        {
            'OH_JOB_INSTALL_GITLAB_WEBHOOKS__ENABLED': '1',
            'OH_JOB_INSTALL_GITLAB_WEBHOOKS__SCHEDULE': '*/5 * * * *',
            'OH_JOB_INSTALL_GITLAB_WEBHOOKS__ACTIVE_DEADLINE_SECONDS': '120',
            'OH_JOB_INSTALL_GITLAB_WEBHOOKS__BACKOFF_LIMIT': '0',
        }
    )

    assert (item.job.schedule, item.job.deadline_seconds, item.job.backoff_limit) == (
        '*/5 * * * *',
        120,
        0,
    )


@pytest.mark.parametrize(
    'key, value',
    [
        ('OH_JOB_RESEND_SYNC__ENABLED', 'yes'),
        ('OH_JOB_RESEND_SYNC__SCHEDULE', 'daily'),
        ('OH_JOB_RESEND_SYNC__SCHEDULE', 'foo bar baz qux quux'),
        ('OH_JOB_RESEND_SYNC__SCHEDULE', '61 * * * *'),
        ('OH_JOB_RESEND_SYNC__BACKOFF_LIMIT', '-1'),
        ('OH_JOB_RESEND_SYNC__ACTIVE_DEADLINE_SECONDS', '0'),
        ('OH_JOB_RESEND_SYNC__ACTIVE_DEADLINE_SECONDS', 'soon'),
        ('OH_JOB_RESEND_SYCN__ENABLED', 'true'),
    ],
)
def test_an_invalid_job_setting_is_a_configuration_error(key, value):
    with pytest.raises(ScheduledJobsConfigError, match=key):
        configure({key: value})


def test_a_jobs_settings_reach_only_its_own_child():
    configured = configure(
        {
            FLAG: 'true',
            'DB_HOST': 'db',
            'OH_JOB_RESEND_SYNC__ENABLED': 'true',
            'OH_JOB_RESEND_SYNC__BATCH_SIZE': '50',
            'OH_JOB_RESEND_SYNC__RESEND_AUDIENCE_ID': 'aud',
            'OH_JOB_BUDGET_MAINTENANCE__ENABLED': 'true',
        }
    )
    env = {item.job.name: item.env for item in configured}

    assert env['resend_sync']['BATCH_SIZE'] == '50'
    assert env['resend_sync']['RESEND_AUDIENCE_ID'] == 'aud'
    assert 'BATCH_SIZE' not in env['budget_maintenance']
    for child_env in env.values():
        assert child_env['DB_HOST'] == 'db'
        assert FLAG not in child_env
        assert not [key for key in child_env if key.startswith('OH_JOB_')]
        assert 'ENABLED' not in child_env


def test_the_reserved_job_settings_never_reach_the_child():
    [item] = configure(
        {
            'OH_JOB_RESEND_SYNC__ENABLED': 'true',
            'OH_JOB_RESEND_SYNC__SCHEDULE': '0 4 * * *',
            'OH_JOB_RESEND_SYNC__BACKOFF_LIMIT': '1',
            'OH_JOB_RESEND_SYNC__ACTIVE_DEADLINE_SECONDS': '60',
        }
    )

    assert not {'SCHEDULE', 'BACKOFF_LIMIT', 'ACTIVE_DEADLINE_SECONDS'} & set(item.env)


def test_blank_values_fall_back_to_the_defaults():
    # A Compose .env file can leave a setting blank.
    assert scheduled_jobs_enabled({FLAG: '   '}) is False
    [item] = configure(
        {
            'OH_JOB_MAINTENANCE_TASKS__ENABLED': 'true',
            'OH_JOB_MAINTENANCE_TASKS__BACKOFF_LIMIT': '',
            'OH_JOB_MAINTENANCE_TASKS__ACTIVE_DEADLINE_SECONDS': ' ',
        }
    )

    assert (item.job.deadline_seconds, item.job.backoff_limit) == (1800, 3)


def test_the_maintenance_lease_follows_the_longer_budget_deadline():
    default = configure({'OH_JOB_BUDGET_MAINTENANCE__ENABLED': 'true'})
    overridden = configure(
        {
            'OH_JOB_BUDGET_MAINTENANCE__ENABLED': 'true',
            'OH_JOB_MAINTENANCE_TASKS__ENABLED': 'true',
            # The disabled job's deadline still counts: both claim one table.
            'OH_JOB_MAINTENANCE_TASKS__ACTIVE_DEADLINE_SECONDS': '3600',
            'OH_JOB_INSTALL_GITLAB_WEBHOOKS__ENABLED': 'true',
        }
    )

    assert default[0].env[LEASE_VARIABLE] == '2100'
    leases = {item.job.name: item.env.get(LEASE_VARIABLE) for item in overridden}
    assert leases == {
        'budget_maintenance': '3900',
        'maintenance_tasks': '3900',
        'install_gitlab_webhooks': None,
    }


def test_every_job_has_its_own_setting_prefix():
    prefixes = [job.prefix for job in JOBS]
    assert len(prefixes) == len(set(prefixes)) == 7
    assert not [a for a in prefixes for b in prefixes if a != b and b.startswith(a)]


def test_each_enabled_job_is_a_locked_periodic_task_on_scheduled_jobs():
    app = App(connector=InMemoryConnector())
    register_scheduled_jobs(
        app,
        configure(
            {
                'OH_JOB_BUDGET_MAINTENANCE__ENABLED': 'true',
                'OH_JOB_PROACTIVE_CONVO_CLEAN__ENABLED': 'true',
                'OH_JOB_PROACTIVE_CONVO_CLEAN__SCHEDULE': '*/15 * * * *',
            }
        ),
    )

    tasks = {
        name: (task.queue, task.lock, task.queueing_lock)
        for name, task in app.tasks.items()
        if name.startswith('scheduled_jobs:')
    }
    assert tasks == {
        'scheduled_jobs:budget_maintenance': (
            'scheduled_jobs',
            'scheduled_jobs:budget_maintenance',
            'scheduled_jobs:budget_maintenance',
        ),
        'scheduled_jobs:proactive_convo_clean': (
            'scheduled_jobs',
            'scheduled_jobs:proactive_convo_clean',
            'scheduled_jobs:proactive_convo_clean',
        ),
    }
    crons = {p.task.name: p.cron for p in app.periodic_registry.periodic_tasks.values()}
    assert crons == {
        'scheduled_jobs:budget_maintenance': '*/15 * * * *',
        'scheduled_jobs:proactive_convo_clean': '*/15 * * * *',
    }


# --- The worker's startup -------------------------------------------------


@pytest.fixture
def started(monkeypatch, caplog):
    """Runs the worker's startup on a fresh app; the worker itself never starts."""
    calls: dict = {}
    fresh = App(connector=PsycopgConnector())

    async def run_worker_async(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(fresh, 'run_worker_async', run_worker_async)
    monkeypatch.setattr(worker_main, 'app', fresh)
    monkeypatch.setattr(openhands_logger, 'propagate', True)
    caplog.set_level(logging.INFO, logger=openhands_logger.name)
    return SimpleNamespace(app=fresh, calls=calls, caplog=caplog)


def _started_record(caplog):
    [record] = [r for r in caplog.records if r.getMessage() == 'worker.started']
    return record


@pytest.mark.usefixtures('app_db_session')
async def test_with_the_flag_off_the_worker_takes_only_default(monkeypatch, started):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv('OH_JOB_BUDGET_MAINTENANCE__ENABLED', 'true')

    await worker_main.run()

    assert started.calls['queues'] == ['default']
    assert not [n for n in started.app.tasks if n.startswith('scheduled_jobs:')]
    record = _started_record(started.caplog)
    assert (record.scheduled_jobs_enabled, record.scheduled_jobs) == (False, [])


@pytest.mark.usefixtures('app_db_session')
async def test_with_the_flag_on_the_worker_also_takes_scheduled_jobs(
    monkeypatch, started
):
    monkeypatch.setenv(FLAG, 'true')
    monkeypatch.setenv('OH_JOB_BUDGET_MAINTENANCE__ENABLED', 'true')

    await worker_main.run()

    assert started.calls['queues'] == ['default', 'scheduled_jobs']
    assert 'scheduled_jobs:budget_maintenance' in started.app.tasks
    record = _started_record(started.caplog)
    assert record.queues == ['default', 'scheduled_jobs']
    assert (record.scheduled_jobs_enabled, record.scheduled_jobs) == (
        True,
        ['budget_maintenance'],
    )


async def test_an_invalid_flag_stops_the_worker_before_it_starts(monkeypatch, started):
    monkeypatch.setenv(FLAG, 'yes')

    with pytest.raises(ScheduledJobsConfigError, match=FLAG):
        await worker_main.run()

    assert started.calls == {}


@pytest.fixture
def restore_signal_handlers():
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.mark.usefixtures('app_db_session', 'restore_signal_handlers')
async def test_a_second_signal_stops_every_running_child(monkeypatch, started):
    """procrastinate restores this handler after it handles the first signal."""
    stops: list[str] = []

    async def stop_all_children() -> None:
        stops.append('stopped')

    async def run_worker_async(**kwargs):
        # What procrastinate leaves installed once it has handled a first signal.
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(worker_main, 'stop_all_children', stop_all_children)
    monkeypatch.setattr(started.app, 'run_worker_async', run_worker_async)
    monkeypatch.delenv(FLAG, raising=False)

    await worker_main.run()

    assert stops == ['stopped']
    assert [r for r in started.caplog.records if r.getMessage() == 'worker.forced_stop']


# --- A real worker on this test's Postgres --------------------------------

PROBE = ScheduledJob('probe', 'scheduled_job_probe', '* * * * *', 30, 0)


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


def _status(conninfo: dict, job_id: int) -> str:
    with psycopg.connect(**conninfo, autocommit=True) as connection:
        return connection.execute(
            'SELECT status FROM procrastinate_jobs WHERE id = %s', (job_id,)
        ).fetchone()[0]


def _probe_app(conninfo: dict, tmp_path: Path, exit_code: int) -> App:
    (tmp_path / 'scheduled_job_probe.py').write_text(
        textwrap.dedent(f"""
            import os, pathlib, sys
            pathlib.Path(os.environ['STATE_DIR'], 'ran').write_text(os.environ['GREETING'])
            sys.exit({exit_code})
        """)
    )
    environ = {
        **os.environ,
        'PYTHONPATH': str(tmp_path),
        'STATE_DIR': str(tmp_path),
        'OH_JOB_PROBE__ENABLED': 'true',
        'OH_JOB_PROBE__GREETING': 'hello',
    }
    app = App(connector=PsycopgConnector(kwargs=conninfo, min_size=1, max_size=3))
    register_scheduled_jobs(app, configure(environ, jobs=(PROBE,)))
    return app


async def test_a_worker_runs_the_jobs_module_with_its_settings(conninfo, tmp_path):
    app = _probe_app(conninfo, tmp_path, exit_code=0)
    async with app.open_async():
        task = app.tasks['scheduled_jobs:probe']
        job_id = await task.defer_async(timestamp=0)
        # One run waiting at most, as Forbid skips a CronJob's schedule.
        with pytest.raises(AlreadyEnqueued):
            await task.defer_async(timestamp=1)
        await app.run_worker_async(queues=['scheduled_jobs'], wait=False)

    assert _status(conninfo, job_id) == 'succeeded'
    assert (tmp_path / 'ran').read_text() == 'hello'


async def test_a_job_whose_module_fails_ends_failed_and_logs_it(
    conninfo, tmp_path, monkeypatch, caplog
):
    # The `openhands` logger stops propagation, so records never reach caplog.
    logger: logging.Logger | None = scheduled_jobs_module._logger
    while logger is not None:
        monkeypatch.setattr(logger, 'propagate', True)
        logger = logger.parent
    app = _probe_app(conninfo, tmp_path, exit_code=4)
    async with app.open_async():
        job_id = await app.tasks['scheduled_jobs:probe'].defer_async(timestamp=0)
        with caplog.at_level(logging.ERROR):
            await app.run_worker_async(queues=['scheduled_jobs'], wait=False)

    assert _status(conninfo, job_id) == 'failed'
    [failed] = [r for r in caplog.records if r.getMessage() == 'scheduled_jobs.failed']
    assert (failed.job, failed.attempts, failed.exit_code) == ('probe', 1, 4)


async def test_a_job_housekeeping_already_ended_starts_no_child(tmp_path, caplog):
    app = App(connector=InMemoryConnector())
    environ = {
        **os.environ,
        'PYTHONPATH': str(tmp_path),
        'STATE_DIR': str(tmp_path),
        'OH_JOB_PROBE__ENABLED': 'true',
    }
    register_scheduled_jobs(app, configure(environ, jobs=(PROBE,)))
    (tmp_path / 'scheduled_job_probe.py').write_text(
        "import pathlib, os\npathlib.Path(os.environ['STATE_DIR'], 'ran').touch()\n"
    )

    async def get_job_status_async(job_id):
        return Status.FAILED

    context = SimpleNamespace(
        job=SimpleNamespace(id=7),
        app=SimpleNamespace(
            job_manager=SimpleNamespace(get_job_status_async=get_job_status_async)
        ),
    )
    await app.tasks['scheduled_jobs:probe'].func(context, timestamp=0)

    assert not (tmp_path / 'ran').exists()


@pytest.mark.parametrize(
    'superseded, message',
    [(False, 'scheduled_jobs.succeeded'), (True, 'scheduled_jobs.superseded')],
)
async def test_the_task_hands_the_jobs_settings_to_the_child_runner(
    monkeypatch, caplog, superseded, message
):
    calls = []

    async def fake_run_child_job(module, **kwargs):
        calls.append((module, kwargs))
        return SimpleNamespace(superseded=superseded, attempts=2)

    monkeypatch.setattr(scheduled_jobs_module, 'run_child_job', fake_run_child_job)
    logger: logging.Logger | None = scheduled_jobs_module._logger
    while logger is not None:
        monkeypatch.setattr(logger, 'propagate', True)
        logger = logger.parent
    [item] = configure(
        {
            'OH_JOB_RESEND_SYNC__ENABLED': 'true',
            'OH_JOB_RESEND_SYNC__ACTIVE_DEADLINE_SECONDS': '120',
            'OH_JOB_RESEND_SYNC__BACKOFF_LIMIT': '5',
        }
    )
    app = App(connector=InMemoryConnector())
    register_scheduled_jobs(app, [item])
    context = SimpleNamespace(job=SimpleNamespace(id=7), app=None)

    with caplog.at_level(logging.INFO):
        await app.tasks['scheduled_jobs:resend_sync'].func(context, timestamp=0)

    [(module, kwargs)] = calls
    assert module == 'sync.resend_keycloak'
    assert (kwargs['deadline_seconds'], kwargs['backoff_limit']) == (120, 5)
    assert kwargs['env'] == item.env
    [record] = [
        r for r in caplog.records if r.getMessage().startswith('scheduled_jobs.')
    ]
    assert (record.getMessage(), record.attempts) == (message, 2)
