"""The worker's housekeeping jobs, run by a real worker on this test's Postgres."""

import contextlib

import psycopg
import pytest
from procrastinate import PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued

from openhands.app_server.worker.app import app

ran: list[str] = []
RECORD_QUEUE = 'test_worker_housekeeping'


@app.task(name='test_worker_housekeeping.record')
async def record(label: str) -> None:
    ran.append(label)


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
async def worker_app(conninfo):
    ran.clear()
    connector = PsycopgConnector(kwargs=conninfo, min_size=1, max_size=3)
    with app.replace_connector(connector):
        async with app.open_async():
            yield app


def _sql(conninfo: dict, query: str, *params) -> list[tuple]:
    with psycopg.connect(**conninfo, autocommit=True) as connection:
        cursor = connection.execute(query, params)
        return cursor.fetchall() if cursor.description else []


def _status(conninfo: dict, job_id: int) -> str:
    return _sql(
        conninfo, 'SELECT status FROM procrastinate_jobs WHERE id = %s', job_id
    )[0][0]


async def _stall(conninfo: dict, label: str, queueing_lock: str) -> int:
    """A job a worker took and then stopped sending heartbeats for."""
    job_id = await record.configure(
        queueing_lock=queueing_lock, queue=RECORD_QUEUE
    ).defer_async(label=label)
    _sql(
        conninfo,
        "UPDATE procrastinate_jobs SET status = 'doing' WHERE id = %s",
        job_id,
    )
    return job_id


async def _run_housekeeping(worker_app, task_name: str) -> None:
    """Run one housekeeping job, and none of the jobs on the record queue."""
    # The worker's own schedule may have queued one already.
    with contextlib.suppress(AlreadyEnqueued):
        await worker_app.configure_task(task_name).defer_async(timestamp=0)
    await _run_worker(worker_app, queues=['default'])


async def _run_recorded_jobs(worker_app) -> None:
    """Run the jobs on the record queue, and none of the housekeeping jobs."""
    await _run_worker(worker_app, queues=[RECORD_QUEUE])


async def _run_worker(worker_app, **options) -> None:
    """Run jobs until none are left."""
    await worker_app.run_worker_async(
        wait=False, install_signal_handlers=False, **options
    )


def test_the_jobs_run_on_a_schedule():
    schedules = {
        periodic_task.task.name: periodic_task.cron
        for periodic_task in app.periodic_registry.periodic_tasks.values()
        if periodic_task.task.name.startswith('worker:')
    }

    assert schedules == {
        'worker:retry_stalled_jobs': '*/10 * * * *',
        'worker:remove_old_jobs': '0 4 * * *',
    }


async def test_a_stalled_job_runs_again(worker_app, conninfo):
    job_id = await _stall(conninfo, 'stalled', queueing_lock='lock-1')

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')

    assert _status(conninfo, job_id) == 'todo'
    await _run_recorded_jobs(worker_app)
    assert ran == ['stalled']
    assert _status(conninfo, job_id) == 'succeeded'


async def test_a_stalled_job_waits_for_a_newer_one(worker_app, conninfo):
    """Both hold the same queueing lock, so both cannot wait in the queue."""
    stalled_id = await _stall(conninfo, 'stalled', queueing_lock='lock-1')
    other_stalled_id = await _stall(conninfo, 'other', queueing_lock='lock-2')
    newer_id = await record.configure(
        queueing_lock='lock-1', queue=RECORD_QUEUE
    ).defer_async(label='newer')

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')

    assert _status(conninfo, stalled_id) == 'doing'
    assert _status(conninfo, other_stalled_id) == 'todo'
    await _run_recorded_jobs(worker_app)
    assert sorted(ran) == ['newer', 'other']
    assert _status(conninfo, newer_id) == 'succeeded'

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')
    await _run_recorded_jobs(worker_app)

    assert ran[-1] == 'stalled'
    assert _status(conninfo, stalled_id) == 'succeeded'


async def test_finished_jobs_are_removed_after_three_days(worker_app, conninfo):
    old_id = await record.defer_async(label='old')
    recent_id = await record.defer_async(label='recent')
    await _run_worker(worker_app)
    _sql(
        conninfo,
        "UPDATE procrastinate_events SET at = now() - interval '4 days' "
        'WHERE job_id = %s',
        old_id,
    )

    await _run_housekeeping(worker_app, 'worker:remove_old_jobs')

    remaining = {row[0] for row in _sql(conninfo, 'SELECT id FROM procrastinate_jobs')}
    assert old_id not in remaining
    assert recent_id in remaining
