"""Queue mechanics against a real database, with real Procrastinate workers.

Only the job bodies are replaced (``job_bodies``); deferral, locking, retry and
the worker loop are Procrastinate's own.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime

import psycopg
import pytest
from procrastinate import App, exceptions
from procrastinate.jobs import Status
from procrastinate.periodic import PeriodicDeferrer

from server.task_queue import worker as worker_module
from server.task_queue.app import build_app
from server.task_queue.config import MAX_ATTEMPTS, Role
from server.task_queue.jobs import JOBS_BY_NAME

ENRICH = JOBS_BY_NAME['enrich_user_interaction_data']
CLEAN = JOBS_BY_NAME['clean_app_conversation_start_tasks']
T0 = 1_780_000_200  # a 10-minute boundary


async def _rows(conninfo: str, query: str, **params) -> list[tuple]:
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        return await (await conn.execute(query, params)).fetchall()


async def _jobs(conninfo: str, task_name: str) -> list[tuple]:
    return await _rows(
        conninfo,
        'SELECT (args->>%(k)s)::bigint, status::text, attempts '
        'FROM procrastinate_jobs WHERE task_name = %(t)s ORDER BY id',
        k='timestamp',
        t=task_name,
    )


async def _drain(settings, app: App) -> None:
    worker = worker_module.build_worker(settings, app)
    worker.wait = False
    await worker.run()


async def _defer_round(app: App, at: float) -> None:
    deferrer = PeriodicDeferrer(registry=app.periodic_registry, **app.periodic_defaults)
    await deferrer.defer_jobs(deferrer.get_previous_tasks(at=at))


class TestOccurrenceUniqueness:
    async def test_replicas_deferring_together_create_one_job_per_occurrence(
        self, make_settings, conninfo
    ):
        apps = [build_app(make_settings()) for _ in range(4)]
        for app in apps:
            await app.open_async()
        try:
            for at in (T0 + 1, T0 + 601, T0 + 1201):
                await asyncio.gather(*(_defer_round(app, at) for app in apps))
        finally:
            for app in apps:
                await app.close_async()

        duplicates = await _rows(
            conninfo,
            "SELECT task_name, args->>'timestamp', count(*) FROM procrastinate_jobs "
            'GROUP BY 1, 2 HAVING count(*) > 1',
        )
        assert duplicates == []
        occurrences = [ts for ts, _, _ in await _jobs(conninfo, ENRICH.task_name)]
        assert occurrences == [T0, T0 + 600, T0 + 1200]

    async def test_replica_with_fast_clock_still_creates_one_job_per_occurrence(
        self, make_settings, conninfo
    ):
        on_time, fast = build_app(make_settings()), build_app(make_settings())
        async with on_time.open_async(), fast.open_async():
            await _defer_round(on_time, at=T0 - 1)
            await _defer_round(fast, at=T0 + 1)  # 2 s ahead
            await _defer_round(on_time, at=T0 + 1)

        occurrences = [ts for ts, _, _ in await _jobs(conninfo, ENRICH.task_name)]
        assert occurrences == [T0 - 600, T0]


class TestRetry:
    async def test_retry_while_next_occurrence_is_queued(
        self, make_settings, conninfo, job_bodies
    ):
        """A fails while B, the next occurrence, waits: A retries, then B runs."""
        runs: list[str] = []
        failures = iter([True])

        async def body() -> None:
            runs.append('run')
            if next(failures, False):
                raise RuntimeError('transient')

        job_bodies[ENRICH.name] = body
        settings = make_settings(scheduling_enabled=False)
        app = build_app(settings)
        async with app.open_async():
            await app.tasks[ENRICH.task_name].defer_async(timestamp=T0)
            await app.tasks[ENRICH.task_name].defer_async(timestamp=T0 + 600)
            await _drain(settings, app)

        assert await _jobs(conninfo, ENRICH.task_name) == [
            (T0, 'succeeded', 2),
            (T0 + 600, 'succeeded', 1),
        ]
        assert len(runs) == 3

    async def test_queueing_lock_would_break_that_retry(self, make_settings):
        """Why no task uses queueing_lock: a retry keeps it and collides with B."""
        app = build_app(make_settings(scheduling_enabled=False))
        async with app.open_async():
            with_queueing_lock = app.configure_task(ENRICH.task_name, queueing_lock='q')
            a = await with_queueing_lock.defer_async(timestamp=T0)
            await app.job_manager.fetch_job(
                queues=None, worker_id=await app.job_manager.register_worker()
            )
            await with_queueing_lock.defer_async(timestamp=T0 + 600)

            with pytest.raises(exceptions.UniqueViolation):
                await app.job_manager.retry_job_by_id_async(
                    a, retry_at=datetime.now(UTC)
                )

    async def test_poison_job_stops_after_max_attempts(
        self, make_settings, conninfo, job_bodies
    ):
        calls = 0

        async def body() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError('always')

        job_bodies[ENRICH.name] = body
        settings = make_settings(scheduling_enabled=False)
        app = build_app(settings)
        async with app.open_async():
            job_id = await app.tasks[ENRICH.task_name].defer_async(timestamp=T0)
            await _drain(settings, app)
            status = await app.job_manager.get_job_status_async(job_id)

        assert status == Status.FAILED
        assert calls == MAX_ATTEMPTS


class TestStaleness:
    async def test_superseded_cleanup_occurrence_is_skipped(
        self, make_settings, conninfo, job_bodies
    ):
        runs = 0

        async def body() -> None:
            nonlocal runs
            runs += 1

        job_bodies[CLEAN.name] = body
        settings = make_settings(scheduling_enabled=False)
        app = build_app(settings)
        async with app.open_async():
            older = await app.tasks[CLEAN.task_name].defer_async(timestamp=T0)
            newer = await app.tasks[CLEAN.task_name].defer_async(timestamp=T0 + 86_400)
            await _drain(settings, app)
            results = await _rows(
                conninfo,
                'SELECT id, status::text FROM procrastinate_jobs ORDER BY id',
            )

        assert results == [(older, 'succeeded'), (newer, 'succeeded')]
        assert runs == 1

    async def test_enrichment_runs_every_late_occurrence(
        self, make_settings, conninfo, job_bodies
    ):
        runs = 0

        async def body() -> None:
            nonlocal runs
            runs += 1

        job_bodies[ENRICH.name] = body
        settings = make_settings(scheduling_enabled=False)
        app = build_app(settings)
        async with app.open_async():
            for n in range(3):
                await app.tasks[ENRICH.task_name].defer_async(timestamp=T0 + 600 * n)
            await _drain(settings, app)

        assert runs == 3


class TestRoles:
    async def _run_briefly(self, settings) -> None:
        app = build_app(settings)
        async with app.open_async():
            worker = worker_module.build_worker(settings, app)
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(1.5)
            worker.stop()
            await task

    async def test_ops_worker_does_not_schedule_business_jobs(
        self, make_settings, conninfo
    ):
        await self._run_briefly(make_settings(role=Role.OPS))
        assert await _rows(conninfo, 'SELECT id FROM procrastinate_jobs') == []

    async def test_business_worker_schedules_on_startup(
        self, make_settings, conninfo, job_bodies
    ):
        for name in JOBS_BY_NAME:
            job_bodies[name] = _noop
        await self._run_briefly(make_settings())
        tasks = await _rows(
            conninfo, 'SELECT DISTINCT task_name FROM procrastinate_jobs'
        )
        assert {name for (name,) in tasks} == {
            job.task_name for job in JOBS_BY_NAME.values()
        }


class TestExit:
    async def _wait_registered(self, conninfo: str) -> None:
        for _ in range(100):
            if await _rows(conninfo, 'SELECT id FROM procrastinate_workers'):
                return
            await asyncio.sleep(0.1)
        raise AssertionError('worker never registered')

    async def _kill_sessions(self, admin: psycopg.AsyncConnection, db: str) -> None:
        await admin.execute(
            'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            'WHERE datname = %(db)s AND pid <> pg_backend_pid()',
            {'db': db},
        )

    async def test_entrypoint_exits_zero_on_sigterm(self, conninfo, test_database):
        server = test_database.server
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(('DB_', 'GCP_', 'TASK_QUEUE_'))
        }
        env.update(
            DB_HOST=server.host,
            DB_PORT=str(server.port),
            DB_NAME=test_database.name,
            DB_USER=server.user,
            DB_PASS=server.password,
            TASK_QUEUE_ROLE='ops',
        )
        process = subprocess.Popen(
            [sys.executable, '-m', 'server.task_queue.worker'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            await self._wait_registered(conninfo)
            process.send_signal(signal.SIGTERM)
            assert await asyncio.to_thread(process.wait, 30) == 0
        finally:
            process.kill()
        assert await _rows(conninfo, 'SELECT id FROM procrastinate_workers') == []

    async def test_worker_survives_a_dropped_connection(
        self, make_settings, conninfo, test_database
    ):
        run = asyncio.create_task(worker_module.run(make_settings(role=Role.OPS)))
        await self._wait_registered(conninfo)
        async with await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True
        ) as admin:
            await self._kill_sessions(admin, test_database.name)

        await asyncio.sleep(2)
        assert not run.done()
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run

    async def test_worker_exits_non_zero_when_the_database_is_unreachable(
        self, make_settings, conninfo, test_database
    ):
        run = asyncio.create_task(worker_module.run(make_settings(role=Role.OPS)))
        await self._wait_registered(conninfo)
        admin_conninfo = psycopg.conninfo.make_conninfo(conninfo, dbname='postgres')
        async with await psycopg.AsyncConnection.connect(
            admin_conninfo, autocommit=True
        ) as admin:
            db = test_database.name
            await admin.execute(f'ALTER DATABASE "{db}" ALLOW_CONNECTIONS false')
            try:
                await self._kill_sessions(admin, db)
                exit_code = await asyncio.wait_for(run, timeout=30)
            finally:
                await admin.execute(f'ALTER DATABASE "{db}" ALLOW_CONNECTIONS true')

        assert exit_code == 1


async def _noop() -> None:
    return None
