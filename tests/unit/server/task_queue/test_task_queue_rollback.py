"""Rollback reconciliation: after it, nothing replays when a worker returns."""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from server.task_queue import rollback
from server.task_queue.app import build_app
from server.task_queue.jobs import JOBS_BY_NAME
from server.task_queue.worker import build_worker

CLEAN = JOBS_BY_NAME['clean_app_conversation_start_tasks']
ENRICH = JOBS_BY_NAME['enrich_user_interaction_data']


@pytest.fixture
def db(conninfo: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def _todo(db, job, ts: int) -> int:
    return db.execute(
        'INSERT INTO procrastinate_jobs (queue_name, task_name, lock, args) '
        'VALUES (%s, %s, %s, %s) RETURNING id',
        (job.role.queue, job.task_name, job.lock, Jsonb({'timestamp': ts})),
    ).fetchone()['id']


def _doing(db, job, ts: int, *, heartbeat_age: str = '1 hour') -> int:
    job_id = _todo(db, job, ts)
    worker_id = db.execute(
        'INSERT INTO procrastinate_workers DEFAULT VALUES RETURNING id'
    ).fetchone()['id']
    db.execute(
        'SELECT procrastinate_fetch_job_v2(%s, %s)', ([job.role.queue], worker_id)
    )
    db.execute(
        'UPDATE procrastinate_workers SET last_heartbeat = now() - %s::interval '
        'WHERE id = %s',
        (heartbeat_age, worker_id),
    )
    return job_id


def _statuses(db) -> dict[int, str]:
    rows = db.execute('SELECT id, status::text FROM procrastinate_jobs').fetchall()
    return {row['id']: row['status'] for row in rows}


def test_reconcile_retires_todo_and_doing_then_the_gate_passes(conninfo, db):
    running = _doing(db, ENRICH, 600)
    queued = _todo(db, CLEAN, 0)
    retrying = _todo(db, ENRICH, 0)
    db.execute(
        "UPDATE procrastinate_jobs SET scheduled_at = now() + interval '1 minute', "
        'attempts = 1 WHERE id = %s',
        (retrying,),
    )

    assert rollback.remaining(conninfo) == {'todo': 2, 'doing': 1}
    result = rollback.reconcile(conninfo)

    assert (result.cancelled, result.failed) == (2, 1)
    assert _statuses(db) == {
        queued: 'cancelled',
        retrying: 'cancelled',
        running: 'failed',
    }
    assert rollback.remaining(conninfo) == {'todo': 0, 'doing': 0}


def test_other_tasks_are_left_alone(conninfo, db):
    other = db.execute(
        'INSERT INTO procrastinate_jobs (queue_name, task_name, args) '
        "VALUES ('business', 'something_else', '{}') RETURNING id"
    ).fetchone()['id']
    rollback.reconcile(conninfo)
    assert _statuses(db) == {other: 'todo'}


def test_refuses_while_a_worker_is_alive(conninfo, db):
    running = _doing(db, ENRICH, 0, heartbeat_age='0 seconds')
    with pytest.raises(rollback.WorkersStillRunning):
        rollback.reconcile(conninfo)
    assert _statuses(db) == {running: 'doing'}


async def test_cancelled_jobs_do_not_replay_when_a_worker_returns(
    conninfo, db, make_settings, job_bodies
):
    runs: list[str] = []

    async def body() -> None:
        runs.append('ran')

    job_bodies[CLEAN.name] = body
    _todo(db, CLEAN, 0)
    _doing(db, CLEAN, 600)
    rollback.reconcile(conninfo)

    settings = make_settings(scheduling_enabled=False)
    app = build_app(settings)
    async with app.open_async():
        worker = build_worker(settings, app)
        worker.wait = False
        await worker.run()

    assert runs == []
    assert rollback.remaining(conninfo) == {'todo': 0, 'doing': 0}


class TestOrdering:
    """Reconciling while something can still retry undoes the sweep."""

    def _retry(self, db, job_id: int) -> None:
        # What a still-running job's failure, or a recovery pass, does.
        db.execute(
            'SELECT procrastinate_retry_job_v2(%s, now(), NULL, NULL, NULL)', (job_id,)
        )

    def test_sweep_before_shutdown_leaves_replayable_work(self, conninfo, db):
        running = _doing(db, ENRICH, 0, heartbeat_age='0 seconds')
        rollback.reconcile(conninfo, live_window_seconds=0)  # guard bypassed
        assert _statuses(db) == {running: 'failed'}

        self._retry(db, running)

        assert _statuses(db) == {running: 'todo'}
        assert rollback.remaining(conninfo) == {'todo': 1, 'doing': 0}

    def test_sweep_after_shutdown_leaves_nothing_to_replay(self, conninfo, db):
        running = _doing(db, ENRICH, 0, heartbeat_age='0 seconds')
        with pytest.raises(rollback.WorkersStillRunning):
            rollback.reconcile(conninfo)

        # The worker is stopped: its last act was the retry, then it is gone.
        self._retry(db, running)
        db.execute('DELETE FROM procrastinate_workers')

        rollback.reconcile(conninfo)
        assert rollback.remaining(conninfo) == {'todo': 0, 'doing': 0}


class TestCli:
    @pytest.fixture(autouse=True)
    def db_env(self, monkeypatch, test_database):
        server = test_database.server
        for key, value in {
            'DB_HOST': server.host,
            'DB_PORT': str(server.port),
            'DB_NAME': test_database.name,
            'DB_USER': server.user,
            'DB_PASS': server.password,
        }.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv('GCP_DB_INSTANCE', raising=False)

    def test_verify_fails_until_reconciled(self, db, capsys):
        _todo(db, CLEAN, 0)
        assert rollback.main(['verify']) == 1
        assert rollback.main(['reconcile']) == 0
        assert rollback.main(['verify']) == 0
        assert capsys.readouterr().out.splitlines() == [
            'todo=1 doing=0',
            'cancelled=1 failed=0',
            'todo=0 doing=0',
        ]

    def test_reconcile_refusal_exits_non_zero(self, db, capsys):
        _doing(db, ENRICH, 0, heartbeat_age='0 seconds')
        assert rollback.main(['reconcile']) == 1
        assert 'refusing to reconcile' in capsys.readouterr().err
