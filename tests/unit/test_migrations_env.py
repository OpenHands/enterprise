"""``migrations/env.py`` opens its engine on a UTC session like the app does.

Data migrations and ``server_default=now()`` columns otherwise evaluate in the
server's default zone. ``env.py`` runs alembic's context at import, so it is
loaded here with a minimal offline stand-in for ``alembic.context``; the engine
it builds then connects to this test's database for real.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
from types import SimpleNamespace

import alembic
import pg8000.dbapi
import pytest
from sqlalchemy import text

from tests import postgres_testdb

ENV_PY = postgres_testdb.MIGRATIONS_DIR / 'env.py'


class _OfflineAlembicContext:
    """Just enough of ``alembic.context`` for ``env.py`` to import."""

    def __init__(self):
        self.config = SimpleNamespace(
            config_file_name=None, get_main_option=lambda _name: None
        )

    def is_offline_mode(self) -> bool:
        return True

    def configure(self, **_kwargs) -> None:
        pass

    @contextlib.contextmanager
    def begin_transaction(self):
        yield

    def run_migrations(self) -> None:
        pass


class _FakeConnector:
    """Opens a plain pg8000 connection, forwarding keyword arguments as the
    real connector's pg8000 helper does."""

    calls: list[dict] = []

    def __init__(self, server: postgres_testdb.PostgresServer):
        self.server = server

    def connect(self, instance: str, driver: str, **kwargs):
        assert driver == 'pg8000'
        type(self).calls.append(dict(kwargs))
        return pg8000.dbapi.connect(
            kwargs.pop('user'),
            host=self.server.host,
            port=self.server.port,
            database=kwargs.pop('db'),
            password=kwargs.pop('password'),
            **kwargs,
        )


@pytest.fixture
def berlin_database(
    test_database: postgres_testdb.TestDatabase,
    postgres_server: postgres_testdb.PostgresServer,
) -> postgres_testdb.TestDatabase:
    postgres_testdb._run_admin_sql(
        postgres_server,
        f'ALTER DATABASE {postgres_testdb._quote(test_database.name)} '
        "SET timezone = 'Europe/Berlin'",
    )
    return test_database


@pytest.fixture
def load_env(monkeypatch):
    """Import ``env.py`` under the given environment; dispose its engine after."""
    modules = []

    def _load(**env: str):
        for key in list(os.environ):
            if key.startswith(('DB_', 'GCP_', 'PG')):
                monkeypatch.delenv(key)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        context = _OfflineAlembicContext()
        monkeypatch.setattr(alembic, 'context', context)
        monkeypatch.setitem(sys.modules, 'alembic.context', context)
        spec = importlib.util.spec_from_file_location(
            'migrations_env_under_test', ENV_PY
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
        return module

    yield _load
    for module in modules:
        module.engine.dispose()


def _url_env(database: postgres_testdb.TestDatabase, **extra: str) -> dict[str, str]:
    server = database.server
    return dict(
        DB_HOST=server.host,
        DB_PORT=str(server.port),
        DB_USER=server.user,
        DB_PASS=server.password,
        DB_NAME=database.name,
        **extra,
    )


def _show_timezone(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text('SHOW TimeZone')).scalar_one()


def test_pg8000_engine_session_is_utc(berlin_database, load_env):
    env = load_env(**_url_env(berlin_database, DB_DRIVER='pg8000'))

    assert env.engine.url.drivername == 'postgresql+pg8000'
    assert _show_timezone(env.engine) == 'UTC'
    assert _show_timezone(env.engine) == 'UTC'


def test_pg8000_engine_keeps_ssl_mode(berlin_database, load_env):
    env = load_env(
        **_url_env(berlin_database, DB_DRIVER='pg8000', DB_SSL_MODE='disable')
    )

    with env.engine.connect() as conn:
        assert conn.execute(text('SHOW TimeZone')).scalar_one() == 'UTC'
        ssl_in_use = conn.execute(
            text('SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()')
        ).scalar_one()
    assert ssl_in_use is False


def test_psycopg2_engine_session_is_utc(berlin_database, load_env):
    env = load_env(**_url_env(berlin_database, DB_DRIVER=''))

    assert env.engine.url.drivername == 'postgresql'
    assert _show_timezone(env.engine) == 'UTC'
    assert _show_timezone(env.engine) == 'UTC'


def test_cloud_sql_engine_session_is_utc(berlin_database, load_env, monkeypatch):
    server = berlin_database.server
    env = load_env(
        DB_USER=server.user,
        DB_PASS=server.password,
        DB_NAME=berlin_database.name,
        GCP_DB_INSTANCE='instance',
        GCP_PROJECT='project',
        GCP_REGION='region',
    )
    _FakeConnector.calls = []
    monkeypatch.setattr(env, 'Connector', lambda: _FakeConnector(server))

    assert _show_timezone(env.engine) == 'UTC'
    assert _FakeConnector.calls == [
        {
            'user': server.user,
            'password': server.password,
            'db': berlin_database.name,
            'startup_params': {'timezone': 'UTC'},
        }
    ]
