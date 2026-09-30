"""Ops recovery against a real database."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from server.task_queue.app import build_app
from server.task_queue.config import MAX_ATTEMPTS
from server.task_queue.jobs import JOBS_BY_NAME
from server.task_queue.maintenance import (
    RECOVERY_LOCK_KEY,
    Decision,
    RecoveryLoop,
    RecoveryPass,
)
from server.task_queue.watchdog import (
    WATCHDOG_EXIT_CODE,
    ExecutionRegistry,
    Telemetry,
    Watchdog,
)
from server.task_queue.worker import build_worker

ENRICH = JOBS_BY_NAME['enrich_user_interaction_data']


@pytest.fixture
def db(conninfo: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def _worker(db: psycopg.Connection, heartbeat_age: str | None = None) -> int:
    worker_id = db.execute(
        'INSERT INTO procrastinate_workers DEFAULT VALUES RETURNING id'
    ).fetchone()['id']
    if heartbeat_age:
        db.execute(
            'UPDATE procrastinate_workers SET last_heartbeat = now() - %s::interval '
            'WHERE id = %s',
            (heartbeat_age, worker_id),
        )
    return worker_id


def _job(
    db: psycopg.Connection, *, queue: str = 'business', attempts: int = 0, ts: int = 0
) -> int:
    return db.execute(
        'INSERT INTO procrastinate_jobs (queue_name, task_name, lock, args, attempts) '
        'VALUES (%s, %s, %s, %s, %s) RETURNING id',
        (queue, ENRICH.task_name, ENRICH.lock, Jsonb({'timestamp': ts}), attempts),
    ).fetchone()['id']


def _stalled_job(
    db: psycopg.Connection, *, queue: str = 'business', attempts: int = 0
) -> tuple[int, int]:
    job_id = _job(db, queue=queue, attempts=attempts)
    worker_id = _worker(db)
    db.execute('SELECT procrastinate_fetch_job_v2(%s, %s)', ([queue], worker_id))
    db.execute(
        "UPDATE procrastinate_workers SET last_heartbeat = now() - interval '1 hour' "
        'WHERE id = %s',
        (worker_id,),
    )
    return job_id, worker_id


def _state(db: psycopg.Connection, job_id: int) -> tuple[str, int]:
    row = db.execute(
        'SELECT status::text, attempts FROM procrastinate_jobs WHERE id = %s',
        (job_id,),
    ).fetchone()
    return row['status'], row['attempts']


def _pass(conninfo: str) -> dict[int, Decision]:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
        with conn.transaction():
            result = RecoveryPass(conn, stalled_seconds=30, batch=100).run()
    assert result.ran
    return result.decisions


def _loop(conninfo: str, registry: ExecutionRegistry | None = None, **kw):
    return RecoveryLoop(
        conninfo,
        registry or ExecutionRegistry(),
        interval=kw.pop('interval', 60),
        stalled_seconds=30,
        pass_budget=kw.pop('pass_budget', 30),
        pass_grace=kw.pop('pass_grace', 10),
        **kw,
    )


class TestDecisions:
    def test_stalled_job_is_requeued(self, conninfo, db):
        job_id, _ = _stalled_job(db)
        assert _pass(conninfo) == {job_id: Decision.RETRIED}
        assert _state(db, job_id) == ('todo', 1)

    def test_last_attempt_is_terminal(self, conninfo, db):
        job_id, _ = _stalled_job(db, attempts=MAX_ATTEMPTS - 1)
        assert _pass(conninfo) == {job_id: Decision.FAILED}
        assert _state(db, job_id) == ('failed', MAX_ATTEMPTS)

    def test_poison_job_is_recovered_at_most_max_attempts_times(self, conninfo, db):
        job_id, _ = _stalled_job(db)
        decisions = []
        for _ in range(MAX_ATTEMPTS + 2):
            decisions += _pass(conninfo).values()
            if _state(db, job_id)[0] == 'todo':
                worker_id = _worker(db)
                db.execute(
                    'SELECT procrastinate_fetch_job_v2(%s, %s)',
                    (['business'], worker_id),
                )
                db.execute(
                    'UPDATE procrastinate_workers '
                    "SET last_heartbeat = now() - interval '1 hour' WHERE id = %s",
                    (worker_id,),
                )
        assert decisions == [Decision.RETRIED] * (MAX_ATTEMPTS - 1) + [Decision.FAILED]
        assert _state(db, job_id)[0] == 'failed'

    def test_job_of_a_live_worker_is_left_alone(self, conninfo, db):
        job_id = _job(db)
        db.execute(
            'SELECT procrastinate_fetch_job_v2(%s, %s)', (['business'], _worker(db))
        )
        assert _pass(conninfo) == {}
        assert _state(db, job_id) == ('doing', 0)

    def test_job_whose_worker_row_was_pruned_is_recovered(self, conninfo, db):
        job_id, worker_id = _stalled_job(db)
        db.execute('DELETE FROM procrastinate_workers WHERE id = %s', (worker_id,))
        assert _pass(conninfo) == {job_id: Decision.RETRIED}

    def test_per_row_queues_are_not_recovered_here(self, conninfo, db):
        job_id, _ = _stalled_job(db, queue='maintenance_rows')
        assert _pass(conninfo) == {}
        assert _state(db, job_id) == ('doing', 0)


class TestRevalidation:
    def test_reassignment_after_selection_is_not_requeued(self, conninfo, db):
        """A resumes and retries, B fetches, then recovery acts: B must survive."""
        job_id, _ = _stalled_job(db)
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
            with conn.transaction():
                recovery = RecoveryPass(conn, stalled_seconds=30, batch=100)
                assert recovery.try_lock()
                [candidate] = recovery.candidates()

                db.execute(
                    'SELECT procrastinate_retry_job_v2(%s, now(), NULL, NULL, NULL)',
                    (job_id,),
                )
                replacement = _worker(db)
                db.execute(
                    'SELECT procrastinate_fetch_job_v2(%s, %s)',
                    (['business'], replacement),
                )

                assert recovery.recover(candidate) is Decision.DROPPED

        row = db.execute(
            'SELECT status::text, worker_id FROM procrastinate_jobs WHERE id = %s',
            (job_id,),
        ).fetchone()
        assert (row['status'], row['worker_id']) == ('doing', replacement)

    def test_worker_that_resumed_after_selection_keeps_its_job(self, conninfo, db):
        job_id, worker_id = _stalled_job(db)
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
            with conn.transaction():
                recovery = RecoveryPass(conn, stalled_seconds=30, batch=100)
                assert recovery.try_lock()
                [candidate] = recovery.candidates()
                db.execute(
                    'UPDATE procrastinate_workers SET last_heartbeat = now() '
                    'WHERE id = %s',
                    (worker_id,),
                )
                assert recovery.recover(candidate) is Decision.DROPPED
        assert _state(db, job_id) == ('doing', 0)

    def test_stale_selection_is_dropped_even_if_the_new_owner_also_stalled(
        self, conninfo, db
    ):
        """Identity, not liveness alone: the next pass decides on fresh state."""
        job_id, _ = _stalled_job(db)
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
            with conn.transaction():
                recovery = RecoveryPass(conn, stalled_seconds=30, batch=100)
                assert recovery.try_lock()
                [candidate] = recovery.candidates()
                db.execute(
                    'SELECT procrastinate_retry_job_v2(%s, now(), NULL, NULL, NULL)',
                    (job_id,),
                )
                replacement = _worker(db)
                db.execute(
                    'SELECT procrastinate_fetch_job_v2(%s, %s)',
                    (['business'], replacement),
                )
                db.execute(
                    'UPDATE procrastinate_workers '
                    "SET last_heartbeat = now() - interval '1 hour' WHERE id = %s",
                    (replacement,),
                )
                assert recovery.recover(candidate) is Decision.DROPPED
        assert _state(db, job_id) == ('doing', 1)

        assert _pass(conninfo) == {job_id: Decision.RETRIED}
        assert _state(db, job_id) == ('todo', 2)


class TestCoordination:
    def test_lock_is_released_after_each_pass(self, conninfo, db):
        loop = _loop(conninfo)
        loop.run_pass()
        held = db.execute(
            "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'"
        ).fetchone()['n']
        assert held == 0
        assert db.execute(
            'SELECT pg_try_advisory_xact_lock(%s) AS ok', (RECOVERY_LOCK_KEY,)
        ).fetchone()['ok']

    def test_a_failed_pass_closes_its_connection(self, conninfo, db, monkeypatch):
        loop = _loop(conninfo)
        loop.run_pass()
        assert loop._conn is not None
        pid = loop._conn.info.backend_pid

        def boom(self):
            raise RuntimeError('pass failed')

        monkeypatch.setattr(RecoveryPass, 'candidates', boom)
        with pytest.raises(RuntimeError):
            loop.run_pass()
        assert loop._conn is None
        alive = db.execute(
            'SELECT count(*) AS n FROM pg_stat_activity WHERE pid = %s', (pid,)
        ).fetchone()['n']
        assert alive == 0

    def test_replica_skips_while_another_pass_holds_the_lock(self, conninfo, db):
        job_id, _ = _stalled_job(db)
        with psycopg.connect(conninfo, autocommit=True) as holder:
            with holder.transaction():
                holder.execute('SELECT pg_advisory_xact_lock(%s)', (RECOVERY_LOCK_KEY,))
                assert _loop(conninfo).run_pass().ran is False
        assert _state(db, job_id) == ('doing', 0)

    def test_recovery_resumes_after_the_lock_holder_is_killed(self, conninfo, db):
        job_id, _ = _stalled_job(db)
        holder = psycopg.connect(conninfo, autocommit=True)
        holder.execute('BEGIN')
        holder.execute('SELECT pg_advisory_xact_lock(%s)', (RECOVERY_LOCK_KEY,))
        assert _loop(conninfo).run_pass().ran is False

        db.execute('SELECT pg_terminate_backend(%s)', (holder.info.backend_pid,))
        holder.close()

        assert _loop(conninfo).run_pass().decisions == {job_id: Decision.RETRIED}


class TestHungPass:
    def test_hung_pass_with_healthy_loop_ends_in_self_exit(
        self, blackhole, live_heartbeat, db
    ):
        proxy, conninfo = blackhole
        registry = ExecutionRegistry()
        loop = _loop(conninfo, registry, pass_budget=0.5, pass_grace=0.5)
        loop.run_pass()  # the dedicated connection is open through the proxy
        proxy.blackholed.set()

        codes: list[int] = []
        exited = threading.Event()
        watchdog = Watchdog(
            registry,
            live_heartbeat,
            Telemetry(),
            check_interval=0.05,
            loop_stall_timeout=60,
            exit_report_wait=0.2,
            exit_fn=lambda code: (codes.append(code), exited.set()),
        )
        watchdog.start()
        threading.Thread(target=loop.run_pass, daemon=True).start()

        assert exited.wait(10)
        assert codes == [WATCHDOG_EXIT_CODE]


class TestStalledPathRetry:
    async def test_stalled_job_retries_while_next_occurrence_is_queued(
        self, conninfo, db, make_settings, job_bodies
    ):
        runs: list[int] = []

        async def body() -> None:
            runs.append(1)

        job_bodies[ENRICH.name] = body
        a, _ = _stalled_job(db)
        b = _job(db, ts=600)

        assert _pass(conninfo) == {a: Decision.RETRIED}

        settings = make_settings(scheduling_enabled=False)
        app = build_app(settings)
        async with app.open_async():
            worker = build_worker(settings, app)
            worker.wait = False
            await worker.run()

        assert _state(db, a) == ('succeeded', 2)
        assert _state(db, b) == ('succeeded', 1)
        assert len(runs) == 2


class TestRetention:
    def _finish(self, db, job_id: int, age: str) -> None:
        db.execute(
            "UPDATE procrastinate_jobs SET status = 'succeeded' WHERE id = %s",
            (job_id,),
        )
        db.execute(
            'UPDATE procrastinate_events SET at = now() - %s::interval '
            'WHERE job_id = %s',
            (age, job_id),
        )

    def test_old_final_jobs_are_deleted_and_the_rest_kept(self, conninfo, db):
        old, recent, pending = _job(db), _job(db, ts=1), _job(db, ts=2)
        self._finish(db, old, '8 days')
        self._finish(db, recent, '1 day')
        db.execute(
            'UPDATE procrastinate_events SET at = now() - %s::interval WHERE job_id = %s',
            ('30 days', pending),
        )

        assert _loop(conninfo).run_pass().deleted == 1
        remaining = {
            r['id'] for r in db.execute('SELECT id FROM procrastinate_jobs').fetchall()
        }
        assert remaining == {recent, pending}

    def test_job_referenced_by_a_periodic_defer_is_kept(self, conninfo, db):
        old = _job(db)
        self._finish(db, old, '8 days')
        db.execute(
            'INSERT INTO procrastinate_periodic_defers '
            '(task_name, periodic_id, defer_timestamp, job_id) VALUES (%s, %s, 0, %s)',
            (ENRICH.task_name, ENRICH.name, old),
        )
        assert _loop(conninfo).run_pass().deleted == 0
