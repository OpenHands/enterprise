"""The worker's database connections, against this test's own Postgres.

The Cloud SQL path runs the Cloud SQL Python Connector's real psycopg driver.
Only the socket differs: the driver is handed a plain TCP socket to the test
Postgres, where ``Connector.connect_async`` would hand it the Cloud SQL TLS
socket.
"""

import asyncio
import contextlib
import gc
import socket
import time
import warnings
from pathlib import Path

import psycopg
import pytest
from google.cloud.sql.connector import psycopg as cloud_sql_psycopg
from procrastinate import App, PsycopgConnector
from pydantic import SecretStr

from openhands.app_server.services.db_session_injector import DbSessionInjector
from openhands.app_server.worker.cloud_sql import CloudSqlDatabase
from openhands.app_server.worker.database import WorkerDatabase
from tests import postgres_testdb


def _cloud_sql_connector(database: CloudSqlDatabase) -> PsycopgConnector:
    return PsycopgConnector(
        connection_class=database.connection_class(), min_size=1, max_size=3
    )


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


class TestFromSettings:
    def test_connects_to_db_host(self):
        database = WorkerDatabase.from_settings(
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

        assert database.cloud_sql is None
        assert database.connector._pool_args['kwargs'] == {
            'host': 'db.internal',
            'port': 6432,
            'user': 'app',
            'password': 'secret',
            'dbname': 'openhands',
            'sslmode': 'require',
        }
        assert database.connector._pool_args['max_size'] == 4

    def test_connects_through_cloud_sql(self):
        database = WorkerDatabase.from_settings(
            DbSessionInjector(
                persistence_dir=Path('/tmp'),
                name='openhands',
                user='app',
                password=SecretStr('secret'),
                gcp_db_instance='instance',
                gcp_project='project',
                gcp_region='region',
            ),
            pool_size=4,
        )

        assert database.cloud_sql is not None
        assert database.cloud_sql.instance_connection_name == 'project:region:instance'
        connection_class = database.connector._pool_args['connection_class']
        assert issubclass(connection_class, psycopg.AsyncConnection)

    def test_refuses_to_start_without_a_database(self):
        with pytest.raises(RuntimeError, match='No database configured'):
            WorkerDatabase.from_settings(
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

        @self.app.task(name='test.ping')
        async def ping(sent_at: float) -> None:
            self.waits.append(time.monotonic() - sent_at)
            self._done.set()

        self.ping = ping

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


async def test_runs_a_job_on_db_host(test_database):
    database = WorkerDatabase.from_settings(_db_settings(test_database), pool_size=3)
    ping_app = PingApp(database.connector)

    async with ping_app.app.open_async(), ping_app.worker():
        await ping_app.run_one_job()

    assert len(ping_app.waits) == 1


@pytest.fixture
def cloud_sql_database(test_database, monkeypatch) -> CloudSqlDatabase:
    """A CloudSqlDatabase whose connector socket is a plain one to the test DB."""
    server = test_database.server
    database = CloudSqlDatabase(
        'project:region:instance',
        user=server.user,
        password=server.password,
        db=test_database.name,
    )
    opened: list[psycopg.Connection] = []

    async def open_sync_connection() -> psycopg.Connection:
        def _open() -> psycopg.Connection:
            remote = socket.create_connection((server.host, server.port))
            return cloud_sql_psycopg.connect(
                server.host,
                remote,
                user=server.user,
                password=server.password,
                db=test_database.name,
            )

        connection = await asyncio.to_thread(_open)
        opened.append(connection)
        return connection

    monkeypatch.setattr(database, 'open_sync_connection', open_sync_connection)
    database.opened = opened  # type: ignore[attr-defined]
    return database


class TestCloudSqlConnection:
    async def test_runs_queries_and_transactions(self, cloud_sql_database):
        connection_class = cloud_sql_database.connection_class()

        async with await connection_class.connect() as connection:
            async with connection.transaction():
                cursor = await connection.execute('SELECT 1 + 1')
                assert await cursor.fetchone() == (2,)
            assert not connection.autocommit

    async def test_applies_the_pool_options(self, cloud_sql_database):
        connection_class = cloud_sql_database.connection_class()

        async with await connection_class.connect(
            autocommit=True,
            prepare_threshold=None,
            row_factory=psycopg.rows.dict_row,
            connect_timeout=5,
        ) as connection:
            assert connection.autocommit
            assert connection.prepare_threshold is None
            cursor = await connection.execute('SELECT 1 AS one')
            assert await cursor.fetchone() == {'one': 1}

    async def test_jobs_arrive_through_listen_notify(self, cloud_sql_database):
        ping_app = PingApp(_cloud_sql_connector(cloud_sql_database))

        # With polling this slow, a job picked up at once came by NOTIFY.
        async with (
            ping_app.app.open_async(),
            ping_app.worker(fetch_job_polling_interval=60),
        ):
            waited = await ping_app.run_one_job()

        assert waited < 5

    async def test_the_worker_reconnects_after_the_server_drops_it(
        self, cloud_sql_database, test_database
    ):
        """A Cloud SQL restart or failover drops every connection at once."""
        ping_app = PingApp(_cloud_sql_connector(cloud_sql_database))
        server = test_database.server

        async with (
            ping_app.app.open_async(),
            ping_app.worker(fetch_job_polling_interval=60),
        ):
            await ping_app.run_one_job()
            opened_before_the_drop = len(cloud_sql_database.opened)
            with psycopg.connect(
                host=server.host,
                port=server.port,
                user=server.user,
                password=server.password,
                dbname=test_database.name,
                autocommit=True,
            ) as admin:
                admin.execute(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                    'WHERE datname = current_database() AND pid <> pg_backend_pid()'
                )
            # Picked up at once, so the LISTEN connection is back as well.
            waited = await ping_app.run_one_job()

        assert waited < 5
        assert len(cloud_sql_database.opened) > opened_before_the_drop

    async def test_closing_leaves_nothing_open(self, cloud_sql_database):
        connection_class = cloud_sql_database.connection_class()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            async with await connection_class.connect():
                pass
            gc.collect()

        assert all(connection.closed for connection in cloud_sql_database.opened)
        assert not [w for w in caught if issubclass(w.category, ResourceWarning)]
