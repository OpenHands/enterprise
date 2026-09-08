"""Postgres databases for tests.

A run starts one postgres container, migrates one template database with
``alembic upgrade head``, then clones that template per test with
``CREATE DATABASE ... TEMPLATE``. Cloning takes about 50ms, so every test can
have its own database.

The container is started by whichever process first needs a database and is
removed by the hooks in the root ``conftest.py`` when the run ends.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from filelock import FileLock
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / 'migrations'
ALEMBIC_INI = REPO_ROOT / 'alembic.ini'

# Kept in step with the migration CI job. Stay on a Debian tag: Alpine's musl
# collations sort text differently from Cloud SQL's glibc ones.
IMAGE = 'postgres:16'
PGDATA = '/var/lib/postgresql/data'
POSTGRES_PORT = 5432

DB_USER = 'openhands'
DB_PASSWORD = 'openhands'
ADMIN_DB = 'postgres'
TEMPLATE_DB = 'oh_template'
TEST_DB_PREFIX = 'oh_test_'

# Every process in a run is handed the same token, so they agree on one
# container without needing to talk to each other.
CONTAINER_PREFIX = 'openhands-test-postgres-'
LOCK_TIMEOUT_SECONDS = 600

RUN_TOKEN = pytest.StashKey[str]()

_IDENTIFIER = re.compile(r'^[a-z0-9_]+$')


class PostgresUnavailableError(RuntimeError):
    """Raised when the test Postgres server can't be started or migrated."""


@dataclass(frozen=True)
class PostgresServer:
    """A reachable Postgres server, without reference to any one database."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)

    def url(self, database: str, *, driver: str = '') -> URL:
        return URL.create(
            f'postgresql+{driver}' if driver else 'postgresql',
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=database,
        )

    def sync_url(self, database: str) -> URL:
        """URL for the driver production uses for synchronous sessions."""
        return self.url(database, driver='pg8000')

    def async_url(self, database: str) -> URL:
        """URL for the driver production uses for asynchronous sessions."""
        return self.url(database, driver='asyncpg')


@dataclass(frozen=True)
class TestDatabase:
    """One test's own database."""

    server: PostgresServer
    name: str

    @property
    def sync_url(self) -> URL:
        return self.server.sync_url(self.name)

    @property
    def async_url(self) -> URL:
        return self.server.async_url(self.name)


def _quote(identifier: str) -> str:
    """Quote an identifier we generated ourselves, refusing anything odd.

    Database names cannot be bound as parameters, so this guards the string
    interpolation in the DDL below.
    """
    if not _IDENTIFIER.match(identifier) or len(identifier.encode()) > 63:
        raise ValueError(f'unsafe database identifier: {identifier!r}')
    return f'"{identifier}"'


def _admin_engine(server: PostgresServer, database: str = ADMIN_DB):
    # CREATE/DROP DATABASE cannot run inside a transaction block, hence
    # AUTOCOMMIT. NullPool keeps no idle connections around.
    return create_engine(
        server.sync_url(database), isolation_level='AUTOCOMMIT', poolclass=NullPool
    )


def _run_admin_sql(server: PostgresServer, *statements: str) -> None:
    engine = _admin_engine(server)
    try:
        with engine.connect() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()


def new_run_token() -> str:
    """Identifies this run's container. Generated once, by the root conftest."""
    return uuid.uuid4().hex[:12]


def run_token(config: pytest.Config) -> str:
    """The token every process in this run shares."""
    worker_input = getattr(config, 'workerinput', None)
    if worker_input is not None:
        return worker_input['pg_run_token']
    return config.stash[RUN_TOKEN]


def _state_path(token: str, suffix: str) -> Path:
    return Path(tempfile.gettempdir()) / f'{CONTAINER_PREFIX}{token}.{suffix}'


def _container_name(token: str) -> str:
    return f'{CONTAINER_PREFIX}{token}'


def _start_container(token: str) -> dict:
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.core.config import testcontainers_config

    # The root conftest removes this container when the run ends. Ryuk would
    # race that, and worse, it reaps when the process that started the
    # container exits, which under xdist is one worker among several.
    testcontainers_config.ryuk_disabled = True

    container = (
        PostgresContainer(
            image=IMAGE,
            username=DB_USER,
            password=DB_PASSWORD,
            dbname=ADMIN_DB,
            driver=None,
        )
        .with_name(_container_name(token))
        # Test data never needs to outlive the container, and ``size`` is used
        # verbatim as the docker tmpfs option string.
        .with_tmpfs_mount(PGDATA, 'size=2g')
        .with_command(
            'postgres '
            '-c fsync=off '
            '-c full_page_writes=off '
            '-c synchronous_commit=off '
            '-c max_connections=200'
        )
    )
    _logger.info('Starting %s', IMAGE)
    try:
        container.start()
    except Exception as e:
        raise PostgresUnavailableError(
            'Could not start the Postgres test container. Is Docker running?'
        ) from e
    return {
        'host': container.get_container_host_ip(),
        'port': int(container.get_exposed_port(POSTGRES_PORT)),
    }


def server_for_run(token: str) -> PostgresServer:
    """The server for this run, starting the container if it isn't up yet."""
    address = _state_path(token, 'json')
    with FileLock(str(_state_path(token, 'lock')), timeout=LOCK_TIMEOUT_SECONDS):
        if not address.exists():
            address.write_text(json.dumps(_start_container(token)))
        data = json.loads(address.read_text())
    return PostgresServer(
        host=data['host'], port=data['port'], user=DB_USER, password=DB_PASSWORD
    )


def remove_server(token: str) -> None:
    """Remove the container for a run. Safe to call when none was started."""
    address = _state_path(token, 'json')
    _state_path(token, 'lock').unlink(missing_ok=True)
    if not address.exists():
        return

    # testcontainers' client, not ``docker.from_env()``: it resolves the
    # daemon socket for Docker Desktop, OrbStack and remote DOCKER_HOST setups.
    from testcontainers.core.docker_client import DockerClient

    name = _container_name(token)
    try:
        DockerClient().client.containers.get(name).remove(force=True)
    except Exception as e:
        # Say so loudly and keep the state file as a breadcrumb. A container
        # outliving the run is the thing this is here to prevent.
        print(f'Could not remove Postgres test container {name}: {e}', file=sys.stderr)
        return
    address.unlink(missing_ok=True)


def template_for_run(server: PostgresServer, token: str) -> str:
    """The migrated template for this run, building it if it isn't there yet."""
    with FileLock(str(_state_path(token, 'lock')), timeout=LOCK_TIMEOUT_SECONDS):
        engine = _admin_engine(server)
        try:
            with engine.connect() as conn:
                exists = _database_exists(conn, TEMPLATE_DB)
        finally:
            engine.dispose()
        if not exists:
            create_template_database(server)
    return TEMPLATE_DB


def _database_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text('SELECT 1 FROM pg_database WHERE datname = :name'), {'name': name}
        ).scalar()
        is not None
    )


def create_template_database(server: PostgresServer) -> str:
    """Migrate the database that per-test databases are cloned from."""
    _logger.info('Migrating template database %s', TEMPLATE_DB)
    _run_admin_sql(
        server,
        f'CREATE DATABASE {_quote(TEMPLATE_DB)} TEMPLATE template0 ENCODING UTF8',
    )
    _run_migrations(server, TEMPLATE_DB)
    _empty_tables(server, TEMPLATE_DB)
    # Refusing connections keeps CREATE DATABASE ... TEMPLATE from tripping over
    # "source database is being accessed by other users".
    _run_admin_sql(
        server, f'ALTER DATABASE {_quote(TEMPLATE_DB)} ALLOW_CONNECTIONS false'
    )
    return TEMPLATE_DB


def _run_migrations(server: PostgresServer, database: str) -> None:
    # Run alembic out of process: importing ``migrations/env.py`` here would
    # reconfigure logging for the whole pytest session (alembic.ini sets the
    # root logger to DEBUG) and cache an engine built from the ambient
    # environment.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(('DB_', 'GCP_', 'PG'))
    }
    env.update(
        DB_HOST=server.host,
        DB_PORT=str(server.port),
        DB_USER=server.user,
        DB_PASS=server.password,
        DB_NAME=database,
        DB_DRIVER='pg8000',
        # Pin the inputs that make migrations branch, so the template is
        # identical on every machine.
        WEB_HOST='',
    )
    env.pop('STRIPE_API_KEY', None)

    result = subprocess.run(
        [sys.executable, '-m', 'alembic', '-c', str(ALEMBIC_INI), 'upgrade', 'head'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PostgresUnavailableError(
            'alembic upgrade head failed while building the test database '
            f'template:\n{result.stdout}\n{result.stderr}'
        )


def _empty_tables(server: PostgresServer, database: str) -> None:
    """Delete the reference rows that migrations seed.

    Migrations 089 and 098 insert the ``role`` and ``verified_models`` rows
    every real deployment has. Tests build the rows they need themselves, often
    with ids and ranks of their own choosing, so the template ships an empty
    schema with sequences back at 1.
    """
    engine = _admin_engine(server, database)
    try:
        with engine.connect() as conn:
            tables = (
                conn.execute(
                    text(
                        'SELECT quote_ident(tablename) FROM pg_tables '
                        "WHERE schemaname = 'public' "
                        'AND tablename <> :version_table'
                    ),
                    {'version_table': 'alembic_version'},
                )
                .scalars()
                .all()
            )
            if tables:
                conn.execute(
                    text(f'TRUNCATE TABLE {", ".join(tables)} RESTART IDENTITY CASCADE')
                )
    finally:
        engine.dispose()


def create_test_database(server: PostgresServer, template: str) -> str:
    """Clone ``template`` into a fresh database and return its name."""
    name = f'{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}'
    _run_admin_sql(
        server, f'CREATE DATABASE {_quote(name)} TEMPLATE {_quote(template)}'
    )
    return name


def drop_test_database(server: PostgresServer, name: str) -> None:
    """Drop a per-test database, disconnecting anything still attached."""
    _run_admin_sql(server, f'DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)')
