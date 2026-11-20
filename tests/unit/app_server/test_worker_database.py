"""The worker's database connections, against this test's own Postgres."""

import asyncio
import contextlib
import time
from pathlib import Path

import psycopg
import pytest
from procrastinate import App
from pydantic import SecretStr

from openhands.app_server.services.db_session_injector import DbSessionInjector
from openhands.app_server.worker.database import build_connector
from tests import postgres_testdb


def _db_settings(test_database: postgres_testdb.TestDatabase) -> DbSessionInjector:
    server = test_database.server
    return DbSessionInjector(
        persistence_dir=Path('/tmp'),
        host=server.host,
        port=server.port,
        name=test_database.name,
        user=server.user,
        password=SecretStr(server.password),
        gcp_db_instance='',
    )


class TestBuildConnector:
    def test_connects_to_db_host(self):
        connector = build_connector(
            DbSessionInjector(
                persistence_dir=Path('/tmp'),
                host='db.internal',
                port=6432,
                name='openhands',
                user='app',
                password=SecretStr('secret'),
                gcp_db_instance='',
                ssl_mode='require',
            ),
            pool_size=4,
        )

        assert connector._pool_args['kwargs'] == {
            'host': 'db.internal',
            'port': 6432,
            'user': 'app',
            'password': 'secret',
            'dbname': 'openhands',
            'sslmode': 'require',
            'options': '-c timezone=UTC',
        }
        assert connector._pool_args['max_size'] == 4

    def test_refuses_to_start_without_a_database(self):
        with pytest.raises(RuntimeError, match='No database configured'):
            build_connector(
                DbSessionInjector(
                    persistence_dir=Path('/tmp'), host='', gcp_db_instance=''
                ),
                pool_size=4,
            )


class PingApp:
    """An app with one job, which records how long it waited to run."""

    def __init__(self, connector):
        self.app = App(connector=connector)
        self.waits: list[float] = []
        self._done = asyncio.Event()
        self._idle = asyncio.Event()

        @self.app.task(name='test.ping')
        async def ping(sent_at: float) -> None:
            self.waits.append(time.monotonic() - sent_at)
            self._done.set()

        self.ping = ping

        # The worker is idle once a fetch finds no job.
        fetch_job = self.app.job_manager.fetch_job

        async def fetch_job_and_track_idle(**kwargs):
            self._idle.clear()
            job = await fetch_job(**kwargs)
            if job is None:
                self._idle.set()
            return job

        self.app.job_manager.fetch_job = fetch_job_and_track_idle

    @contextlib.asynccontextmanager
    async def worker(self, **worker_options):
        """Run a worker until the block ends."""
        worker = asyncio.create_task(
            self.app.run_worker_async(install_signal_handlers=False, **worker_options)
        )
        try:
            # Give the worker time to open its LISTEN connection.
            await asyncio.sleep(1)
            yield
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def run_one_job(self) -> float:
        """Defer a job, and return how long it waited to run."""
        self._done.clear()
        await self.ping.defer_async(sent_at=time.monotonic())
        await asyncio.wait_for(self._done.wait(), timeout=20)
        return self.waits[-1]

    async def wait_until_idle(self) -> None:
        """Wait until the worker has finished its jobs and found no more."""
        await asyncio.wait_for(self._idle.wait(), timeout=20)


async def test_worker_session_is_utc_on_a_non_utc_database(
    test_database, postgres_server
):
    postgres_testdb._run_admin_sql(
        postgres_server,
        f'ALTER DATABASE {postgres_testdb._quote(test_database.name)} '
        "SET timezone = 'America/Chicago'",
    )
    connector = build_connector(_db_settings(test_database), pool_size=1)

    await connector.open_async()
    try:
        rows = await connector.execute_query_all_async('SHOW TimeZone')
    finally:
        await connector.close_async()

    assert rows == [{'TimeZone': 'UTC'}]


async def test_jobs_arrive_through_listen_notify(test_database):
    ping_app = PingApp(build_connector(_db_settings(test_database), pool_size=3))

    # With polling this slow, a job picked up at once came by NOTIFY.
    async with (
        ping_app.app.open_async(),
        ping_app.worker(fetch_job_polling_interval=60),
    ):
        waited = await ping_app.run_one_job()

    assert waited < 5


async def _wait_for_new_listener(admin: psycopg.AsyncConnection) -> None:
    """Wait until a connection opened after `admin` is listening for jobs."""
    async with asyncio.timeout(10):
        while True:
            cursor = await admin.execute(
                'SELECT 1 FROM pg_stat_activity '
                'WHERE datname = current_database() '
                "AND state = 'idle' AND starts_with(query, 'LISTEN ') "
                'AND backend_start > (SELECT backend_start FROM pg_stat_activity '
                'WHERE pid = pg_backend_pid())'
            )
            if await cursor.fetchone():
                return
            await asyncio.sleep(0.05)


async def test_the_worker_reconnects_after_the_server_drops_it(test_database):
    """A database restart or failover drops every connection at once."""
    ping_app = PingApp(build_connector(_db_settings(test_database), pool_size=3))
    server = test_database.server

    async with (
        ping_app.app.open_async(),
        ping_app.worker(fetch_job_polling_interval=60),
    ):
        await ping_app.run_one_job()
        # procrastinate stops the worker if a query fails mid-flight, so drop
        # the connections while it waits for jobs.
        await ping_app.wait_until_idle()
        async with await psycopg.AsyncConnection.connect(
            host=server.host,
            port=server.port,
            user=server.user,
            password=server.password,
            dbname=test_database.name,
            autocommit=True,
        ) as admin:
            await admin.execute(
                'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                'WHERE datname = current_database() AND pid <> pg_backend_pid()'
            )
            # A job deferred before the worker listens again waits for the
            # next poll, so wait for the new LISTEN connection first.
            await _wait_for_new_listener(admin)
        # Picked up at once, so the notification reached the new connection.
        waited = await ping_app.run_one_job()

    assert waited < 5
