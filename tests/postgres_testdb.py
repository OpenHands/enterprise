"""Real-Postgres test databases, served from one container per machine.

Unit tests used to run against SQLite, which drifts from the production
Postgres schema far enough that models grew SQLite-only branches (see the
``scopes`` column in ``storage.gitlab_webhook``). These helpers hand every
test its own real Postgres database instead.

There are three layers, arranged so that per-test isolation does not cost
per-test startup time:

1. **Container** -- one per machine, named ``openhands-test-postgres``,
   started on first use and deliberately *left running* so later pytest
   invocations reuse it. Its data lives on tmpfs with fsync off; it is
   disposable by design. Remove it with ``make test-db-down``.
2. **Template database** -- built once by running ``alembic upgrade head``,
   then frozen. Its name embeds a hash of ``migrations/``, so switching
   branches or editing a migration rebuilds it automatically.
3. **Test database** -- cloned per test via ``CREATE DATABASE ... TEMPLATE``,
   which copies the template's files directly (~50ms, no migrations to
   replay) and is dropped when the test finishes.

Work that must happen only once (container start, template build) runs inside
a file lock at a fixed path, so pytest-xdist workers -- and even concurrent
pytest runs -- cooperate instead of racing.

Environment overrides:

``OH_TEST_POSTGRES_URL``
    Use an already-running server instead of starting a container, e.g.
    ``postgresql://openhands:openhands@localhost:5432/postgres``. The database
    in the URL is only used for administrative connections.
``OH_TEST_POSTGRES_IMAGE``
    Image to run (default ``postgres:16``, matching the migration CI job).
    Stay on a Debian-based tag: Alpine's musl collations sort text differently
    from Cloud SQL's glibc ones.
``OH_TEST_POSTGRES_CONTAINER``
    Container name, and therefore the unit of sharing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psutil
from filelock import FileLock
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / 'migrations'
ALEMBIC_INI = REPO_ROOT / 'alembic.ini'

IMAGE = os.environ.get('OH_TEST_POSTGRES_IMAGE', 'postgres:16')
CONTAINER_NAME = os.environ.get('OH_TEST_POSTGRES_CONTAINER', 'openhands-test-postgres')
PGDATA = '/var/lib/postgresql/data'
POSTGRES_PORT = 5432

DB_USER = 'openhands'
DB_PASSWORD = 'openhands'
ADMIN_DB = 'postgres'

TEMPLATE_PREFIX = 'oh_template_'
TEST_DB_PREFIX = 'oh_test_'
BUILDING_SUFFIX = '_building'

# A fixed path, not a pytest tmpdir: the lock has to be shared by every xdist
# worker of this run *and* by any other pytest run on this machine, since they
# all share one container.
LOCK_PATH = Path(tempfile.gettempdir()) / f'{CONTAINER_NAME}.lock'
LOCK_TIMEOUT_SECONDS = 600

_IDENTIFIER = re.compile(r'^[a-z0-9_]+$')

_server: PostgresServer | None = None
_template: str | None = None


class PostgresUnavailableError(RuntimeError):
    """Raised when no Postgres server can be reached or started."""


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
    """One test's own database on the shared server."""

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


def _admin_engine(server: PostgresServer):
    # CREATE/DROP DATABASE cannot run inside a transaction block, hence
    # AUTOCOMMIT. NullPool keeps no idle connections to the admin database.
    return create_engine(
        server.sync_url(ADMIN_DB), isolation_level='AUTOCOMMIT', poolclass=NullPool
    )


def _run_admin_sql(server: PostgresServer, *statements: str) -> None:
    engine = _admin_engine(server)
    try:
        with engine.connect() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()


def _database_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text('SELECT 1 FROM pg_database WHERE datname = :name'), {'name': name}
        ).scalar()
        is not None
    )


# ---------------------------------------------------------------------------
# The container
# ---------------------------------------------------------------------------


def _docker_client():
    from testcontainers.core.docker_client import DockerClient

    try:
        return DockerClient()
    except Exception as e:  # pragma: no cover - depends on the local machine
        raise PostgresUnavailableError(
            'Could not talk to Docker, which these tests need in order to run '
            'Postgres. Start Docker, or point OH_TEST_POSTGRES_URL at a '
            'Postgres server you already have.'
        ) from e


def _server_from_env() -> PostgresServer | None:
    raw = os.environ.get('OH_TEST_POSTGRES_URL')
    if not raw:
        return None
    url = make_url(raw)
    if not url.host:
        raise PostgresUnavailableError(
            f'OH_TEST_POSTGRES_URL has no host: {raw!r}',
        )
    return PostgresServer(
        host=url.host,
        port=url.port or POSTGRES_PORT,
        user=url.username or DB_USER,
        password=url.password or DB_PASSWORD,
    )


def _wait_until_reachable(server: PostgresServer, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        engine = _admin_engine(server)
        try:
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            return True
        except Exception:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)
        finally:
            engine.dispose()


def _existing_container(client):
    from docker.errors import NotFound

    try:
        return client.client.containers.get(CONTAINER_NAME)
    except NotFound:
        return None


def _discover_server(client) -> PostgresServer | None:
    """Return the server backing an already-running container, if any."""
    container = _existing_container(client)
    if container is None:
        return None
    if container.status != 'running':
        _logger.info(
            'Removing %s container (status %s)', CONTAINER_NAME, container.status
        )
        container.remove(force=True)
        return None
    if IMAGE not in (container.image.tags or []):
        _logger.info(
            'Recreating %s container: running %s, wanted %s',
            CONTAINER_NAME,
            container.image.tags,
            IMAGE,
        )
        container.remove(force=True)
        return None

    server = PostgresServer(
        host=client.host(),
        port=int(client.port(container.id, POSTGRES_PORT)),
        user=DB_USER,
        password=DB_PASSWORD,
    )
    # A container can be running with a dead or still-initialising Postgres
    # inside it. Give it a moment, then start over rather than failing every
    # test with a connection error.
    if _wait_until_reachable(server, timeout=15):
        return server
    _logger.warning(
        'Container %s is running but Postgres is unreachable; recreating it',
        CONTAINER_NAME,
    )
    container.remove(force=True)
    return None


def _start_container() -> PostgresServer:
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.core.config import testcontainers_config

    # We keep the container alive between runs, so Ryuk must not reap it when
    # this process exits.
    testcontainers_config.ryuk_disabled = True

    container = (
        PostgresContainer(
            image=IMAGE,
            username=DB_USER,
            password=DB_PASSWORD,
            dbname=ADMIN_DB,
            driver=None,
        )
        .with_name(CONTAINER_NAME)
        # ``size`` is used verbatim as the docker tmpfs option string, so pass
        # the whole option list. Test data never needs to survive a restart.
        .with_tmpfs_mount(PGDATA, 'size=2g')
        .with_command(
            'postgres '
            '-c fsync=off '
            '-c full_page_writes=off '
            '-c synchronous_commit=off '
            '-c max_connections=200'
        )
    )
    _logger.info('Starting %s container from %s', CONTAINER_NAME, IMAGE)
    container.start()
    return PostgresServer(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(POSTGRES_PORT)),
        user=DB_USER,
        password=DB_PASSWORD,
    )


def shared_server() -> PostgresServer:
    """The Postgres server every test in this process shares."""
    global _server
    if _server is not None:
        return _server

    server = _server_from_env()
    if server is None:
        with FileLock(str(LOCK_PATH), timeout=LOCK_TIMEOUT_SECONDS):
            client = _docker_client()
            server = _discover_server(client) or _start_container()
    elif not _wait_until_reachable(server, timeout=15):
        raise PostgresUnavailableError(
            f'Could not connect to the Postgres server in '
            f'OH_TEST_POSTGRES_URL ({server.host}:{server.port}).'
        )

    _server = server
    return server


# ---------------------------------------------------------------------------
# The template database
# ---------------------------------------------------------------------------


def _template_fingerprint() -> str:
    """Hash everything that decides what the template ends up containing.

    That includes this module, so changing how the template is built discards
    the old one instead of quietly reusing it.
    """
    digest = hashlib.sha256()
    digest.update(IMAGE.encode())
    for path in sorted((MIGRATIONS_DIR / 'versions').glob('*.py')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update((MIGRATIONS_DIR / 'env.py').read_bytes())
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()[:16]


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
        # identical on every machine and the fingerprint above stays honest.
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
    every real deployment has. Tests build the rows they need themselves --
    often with ids and ranks of their own choosing -- so the template ships an
    empty schema with sequences back at 1, and a test that wants production's
    reference data inserts it explicitly.
    """
    engine = create_engine(
        server.sync_url(database), isolation_level='AUTOCOMMIT', poolclass=NullPool
    )
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


def _sweep_stale_databases(conn: Connection, keep_template: str) -> None:
    """Drop leftovers from earlier runs. Best effort; never fatal.

    Test databases are named after the process that made them, so a database
    whose process is gone is safe to drop even while another pytest run is
    using the same container.
    """
    names = conn.execute(
        text(
            'SELECT datname FROM pg_database '
            'WHERE datname LIKE :template OR datname LIKE :test'
        ),
        {'template': f'{TEMPLATE_PREFIX}%', 'test': f'{TEST_DB_PREFIX}%'},
    ).scalars()

    for name in list(names):
        if name == keep_template:
            continue
        if name.startswith(TEST_DB_PREFIX) and _owner_process_alive(name):
            continue
        try:
            conn.execute(text(f'DROP DATABASE {_quote(name)} WITH (FORCE)'))
            _logger.info('Dropped stale test database %s', name)
        except Exception as e:
            _logger.debug('Could not drop stale database %s: %s', name, e)


def _owner_process_alive(name: str) -> bool:
    """Whether the pytest process that created ``name`` is still running."""
    pid = name[len(TEST_DB_PREFIX) :].split('_')[0]
    if not pid.isdigit():
        return True  # unrecognised name; leave it alone
    return psutil.pid_exists(int(pid))


def shared_template(server: PostgresServer) -> str:
    """Name of the migrated database that per-test databases are cloned from."""
    global _template
    if _template is not None:
        return _template

    name = f'{TEMPLATE_PREFIX}{_template_fingerprint()}'
    with FileLock(str(LOCK_PATH), timeout=LOCK_TIMEOUT_SECONDS):
        if not _template_ready(server, name):
            _build_template(server, name)

    _template = name
    return name


def _template_ready(server: PostgresServer, name: str) -> bool:
    """Whether ``name`` already exists, sweeping earlier templates if not."""
    engine = _admin_engine(server)
    try:
        with engine.connect() as conn:
            if _database_exists(conn, name):
                return True
            _sweep_stale_databases(conn, keep_template=name)
            return False
    finally:
        engine.dispose()


def _build_template(server: PostgresServer, name: str) -> None:
    # Migrate under a scratch name and rename on success, so a failed or
    # interrupted build can never be mistaken for a finished template.
    scratch = f'{name}{BUILDING_SUFFIX}_{uuid.uuid4().hex[:8]}'
    _logger.info('Building test database template %s', name)
    started = time.monotonic()

    _run_admin_sql(
        server, f'CREATE DATABASE {_quote(scratch)} TEMPLATE template0 ENCODING UTF8'
    )
    try:
        _run_migrations(server, scratch)
        _empty_tables(server, scratch)
    except Exception:
        _run_admin_sql(
            server, f'DROP DATABASE IF EXISTS {_quote(scratch)} WITH (FORCE)'
        )
        raise

    _run_admin_sql(
        server,
        f'ALTER DATABASE {_quote(scratch)} RENAME TO {_quote(name)}',
        # Refusing connections guarantees CREATE DATABASE ... TEMPLATE never
        # trips over "source database is being accessed by other users".
        f'ALTER DATABASE {_quote(name)} ALLOW_CONNECTIONS false',
    )
    _logger.info(
        'Built test database template %s in %.1fs', name, time.monotonic() - started
    )


# ---------------------------------------------------------------------------
# Per-test databases
# ---------------------------------------------------------------------------


def create_test_database(server: PostgresServer, template: str) -> str:
    """Clone ``template`` into a fresh database and return its name."""
    name = f'{TEST_DB_PREFIX}{os.getpid()}_{uuid.uuid4().hex[:12]}'
    statement = f'CREATE DATABASE {_quote(name)} TEMPLATE {_quote(template)}'
    last_error: Exception | None = None
    # Postgres serialises copies from the same template; under xdist a burst of
    # workers can occasionally still be turned away.
    for attempt in range(5):
        try:
            _run_admin_sql(server, statement)
            return name
        except Exception as e:
            last_error = e
            time.sleep(0.1 * (attempt + 1))
    raise PostgresUnavailableError(
        f'Could not create test database from template {template}'
    ) from last_error


def drop_test_database(server: PostgresServer, name: str) -> None:
    """Drop a per-test database, disconnecting anything still attached."""
    _run_admin_sql(server, f'DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)')
