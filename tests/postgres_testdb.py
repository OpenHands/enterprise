"""Postgres databases for tests.

Each pytest process runs a postgres container, migrates one template database
with ``alembic upgrade head``, then clones that template per test with
``CREATE DATABASE ... TEMPLATE``. Cloning takes about 50ms, so every test can
have its own database. The container is removed when the process exits.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, text
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


@contextmanager
def running_server() -> Iterator[PostgresServer]:
    """Run a postgres container for the duration of the block."""
    from testcontainers.community.postgres import PostgresContainer

    container = (
        PostgresContainer(
            image=IMAGE,
            username=DB_USER,
            password=DB_PASSWORD,
            dbname=ADMIN_DB,
            driver=None,
        )
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
    try:
        yield PostgresServer(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(POSTGRES_PORT)),
            user=DB_USER,
            password=DB_PASSWORD,
        )
    finally:
        container.stop()


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
