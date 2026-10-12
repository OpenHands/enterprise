"""The worker's housekeeping jobs, run by a real worker on this test's Postgres."""

import contextlib
import logging

import psycopg
import pytest
from procrastinate import PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued

from openhands.app_server.worker import housekeeping as housekeeping_module
from openhands.app_server.worker.app import app
from openhands.app_server.worker.housekeeping import SCHEDULED_JOBS_QUEUE

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


async def _stall(
    conninfo: dict,
    label: str,
    queueing_lock: str,
    queue: str = RECORD_QUEUE,
    lock: str | None = None,
) -> int:
    """A job a worker took and then stopped sending heartbeats for."""
    job_id = await record.configure(
        queueing_lock=queueing_lock, queue=queue, lock=lock
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


@pytest.fixture
def worker_logs(monkeypatch, caplog):
    # The `openhands` logger stops propagation, so records never reach the root
    # logger, where caplog listens.
    logger: logging.Logger | None = housekeeping_module._logger
    while logger is not None:
        monkeypatch.setattr(logger, 'propagate', True)
        logger = logger.parent
    caplog.set_level(logging.INFO, logger=housekeeping_module._logger.name)

    def messages(level: int = logging.INFO) -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if r.name == housekeeping_module.__name__ and r.levelno == level
        ]

    return messages


async def test_a_stalled_scheduled_job_ends_failed_and_its_next_tick_runs(
    worker_app, conninfo, worker_logs, caplog
):
    """Retrying the stalled job would collide with the next run's queueing lock."""
    stalled_id = await _stall(
        conninfo,
        'stalled',
        queueing_lock='scheduled_jobs:job',
        queue=SCHEDULED_JOBS_QUEUE,
        lock='scheduled_jobs:job',
    )
    tick_id = await record.configure(
        queueing_lock='scheduled_jobs:job',
        queue=SCHEDULED_JOBS_QUEUE,
        lock='scheduled_jobs:job',
    ).defer_async(label='tick')

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')

    assert _status(conninfo, stalled_id) == 'failed'
    assert worker_logs(logging.ERROR) == ['scheduled_jobs.stalled']
    [stalled_log] = [
        r for r in caplog.records if r.getMessage() == 'scheduled_jobs.stalled'
    ]
    assert (stalled_log.job_id, stalled_log.task_name) == (
        stalled_id,
        'test_worker_housekeeping.record',
    )
    await _run_worker(worker_app, queues=[SCHEDULED_JOBS_QUEUE])
    assert ran == ['tick']
    assert _status(conninfo, tick_id) == 'succeeded'


async def test_a_stalled_scheduled_job_that_finished_first_keeps_its_outcome(
    worker_app, conninfo, worker_logs, monkeypatch
):
    finished_id = await _stall(
        conninfo, 'finished', queueing_lock='lock-1', queue=SCHEDULED_JOBS_QUEUE
    )
    stalled_id = await _stall(
        conninfo, 'stalled', queueing_lock='lock-2', queue=SCHEDULED_JOBS_QUEUE
    )
    job_manager = worker_app.job_manager
    get_stalled_jobs = job_manager.get_stalled_jobs

    async def finish_one_after_the_scan(**kwargs):
        jobs = sorted(await get_stalled_jobs(**kwargs), key=lambda job: job.id)
        _sql(
            conninfo,
            "UPDATE procrastinate_jobs SET status = 'succeeded' WHERE id = %s",
            finished_id,
        )
        return jobs

    monkeypatch.setattr(job_manager, 'get_stalled_jobs', finish_one_after_the_scan)

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')

    assert _status(conninfo, finished_id) == 'succeeded'
    assert _status(conninfo, stalled_id) == 'failed'
    assert 'scheduled_jobs.stalled_already_finished' in worker_logs()
    assert worker_logs(logging.ERROR) == ['scheduled_jobs.stalled']


async def test_a_stalled_scheduled_job_that_cannot_be_ended_does_not_stop_the_others(
    worker_app, conninfo, worker_logs
):
    broken_id = await _stall(
        conninfo, 'broken', queueing_lock='lock-1', queue=SCHEDULED_JOBS_QUEUE
    )
    stalled_id = await _stall(
        conninfo, 'stalled', queueing_lock='lock-2', queue=SCHEDULED_JOBS_QUEUE
    )
    _sql(
        conninfo,
        'CREATE FUNCTION refuse_to_end() RETURNS trigger LANGUAGE plpgsql AS $$ '
        "BEGIN RAISE EXCEPTION 'database unavailable'; END $$",
    )
    _sql(
        conninfo,
        'CREATE TRIGGER refuse_to_end BEFORE UPDATE OF status ON procrastinate_jobs '
        f'FOR EACH ROW WHEN (OLD.id = {broken_id}) EXECUTE FUNCTION refuse_to_end()',
    )

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')

    assert _status(conninfo, broken_id) == 'doing'
    assert _status(conninfo, stalled_id) == 'failed'
    assert 'scheduled_jobs.stalled_already_finished' not in worker_logs()
    assert sorted(worker_logs(logging.ERROR)) == [
        'scheduled_jobs.stalled',
        'scheduled_jobs.stalled_end_failed',
    ]


async def test_stalled_jobs_on_other_queues_are_still_retried(worker_app, conninfo):
    other_id = await _stall(conninfo, 'other', queueing_lock='lock-1')
    scheduled_id = await _stall(
        conninfo, 'scheduled', queueing_lock='lock-2', queue=SCHEDULED_JOBS_QUEUE
    )

    await _run_housekeeping(worker_app, 'worker:retry_stalled_jobs')

    assert _status(conninfo, other_id) == 'todo'
    assert _status(conninfo, scheduled_id) == 'failed'
