import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from logging.config import fileConfig

# Suppress alembic.runtime.plugins INFO logs during import to prevent non-JSON logs in production
# These plugin setup messages would otherwise appear before logging is configured
logging.getLogger('alembic.runtime.plugins').setLevel(logging.WARNING)

# Prevent SQLAlchemy engine from logging SQL results at DEBUG level, which can
# leak sensitive column data (e.g. API keys, tokens) into log aggregators.
# This is set before any engine is created so it takes effect immediately.
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine.Engine').setLevel(logging.WARNING)

from alembic import context  # noqa: E402
from google.cloud.sql.connector import Connector  # noqa: E402
from sqlalchemy import Connection, Engine, create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from openhands.db.ssl import build_db_url_query, build_pg8000_connect_args  # noqa: E402
from storage.base import Base  # noqa: E402

target_metadata = Base.metadata

DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASS = os.getenv('DB_PASS', 'postgres')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'openhands')
# Driver for the non-Cloud-SQL (DB_HOST) path used by feature/local/CI. Production
# connects through the Cloud SQL connector on pg8000, so we default to pg8000 here
# too: this keeps every environment on the same driver and lets pg8000-specific
# migration failures surface before deploy. Set DB_DRIVER='' to use psycopg2.
DB_DRIVER = os.getenv('DB_DRIVER', 'pg8000')
DB_SSL_MODE = os.getenv('DB_SSL_MODE') or os.getenv('PGSSLMODE')

GCP_DB_INSTANCE = os.getenv('GCP_DB_INSTANCE')
GCP_PROJECT = os.getenv('GCP_PROJECT')
GCP_REGION = os.getenv('GCP_REGION')

# Create DB_NAME before migrating if it does not exist. The database user needs
# the CREATEDB privilege.
CREATE_DATABASE_IF_MISSING = os.getenv(
    'CREATE_DATABASE_IF_MISSING', 'false'
).lower() in ('true', '1')
# CREATE DATABASE runs from here. Every PostgreSQL server has this database.
MAINTENANCE_DB_NAME = 'postgres'

# The role the task queue worker connects as (server/task_queue/config.py). It
# must already exist. It gets DML on the queue tables and nothing else, so a
# worker cannot change the schema.
TASK_QUEUE_DB_USER = os.getenv('TASK_QUEUE_DB_USER', '').strip()

logger = logging.getLogger('alembic.env')


@contextmanager
def migration_engine(database: str = DB_NAME) -> Iterator[Engine]:
    """Yield an engine for one migration run, then close everything it opened.

    The app can run migrations inside its own process on startup, so nothing may
    outlive the run. A pooled connection would keep holding the advisory lock.
    """
    connector = None
    if GCP_DB_INSTANCE:
        connector = Connector()
        instance_string = f'{GCP_PROJECT}:{GCP_REGION}:{GCP_DB_INSTANCE}'

        def get_db_connection():
            return connector.connect(
                instance_string,
                'pg8000',
                user=DB_USER,
                password=DB_PASS.strip(),
                db=database,
            )

        engine = create_engine(
            'postgresql+pg8000://', creator=get_db_connection, poolclass=NullPool
        )
    else:
        scheme = f'postgresql+{DB_DRIVER}' if DB_DRIVER else 'postgresql'
        url = f'{scheme}://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{database}'
        if DB_DRIVER != 'pg8000':
            url += build_db_url_query(DB_SSL_MODE)
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args=(
                build_pg8000_connect_args(DB_SSL_MODE) if DB_DRIVER == 'pg8000' else {}
            ),
        )
    try:
        yield engine
    finally:
        engine.dispose()
        if connector is not None:
            connector.close()


def create_database_if_missing() -> None:
    """Create DB_NAME if it does not exist yet."""
    with (
        migration_engine(MAINTENANCE_DB_NAME) as engine,
        engine.connect() as connection,
    ):
        # CREATE DATABASE cannot run inside a transaction.
        connection.execution_options(isolation_level='AUTOCOMMIT')
        # Replicas that start together take turns, so only the first one creates
        # the database. Lock number is the md5 hash of
        # 'openhands_enterprise_create_database'.
        connection.execute(text('SELECT pg_advisory_lock(1655053006352720504)'))
        exists = connection.execute(
            text('SELECT 1 FROM pg_database WHERE datname = :name'),
            {'name': DB_NAME},
        ).scalar()
        if not exists:
            logger.info('Creating database %s', DB_NAME)
            name = connection.dialect.identifier_preparer.quote(DB_NAME)
            connection.exec_driver_sql(f'CREATE DATABASE {name}')


def grant_task_queue_role(connection: Connection) -> None:
    """Grant TASK_QUEUE_DB_USER DML on every Procrastinate table and sequence.

    Runs after every migration run, not in the revision that creates the tables:
    the role may be set up after that revision was applied, and later
    Procrastinate revisions add tables and sequences of their own.
    """
    rows = connection.execute(
        text(
            'SELECT relkind, quote_ident(relname) FROM pg_class'
            ' WHERE relnamespace = current_schema()::regnamespace'
            " AND relkind IN ('r', 'S') AND starts_with(relname, 'procrastinate_')"
        )
    ).all()
    tables = ', '.join(name for kind, name in rows if kind == 'r')
    sequences = ', '.join(name for kind, name in rows if kind == 'S')
    role = connection.dialect.identifier_preparer.quote(TASK_QUEUE_DB_USER)
    if tables:
        connection.exec_driver_sql(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {tables} TO {role}'
        )
    if sequences:
        connection.exec_driver_sql(
            f'GRANT USAGE, SELECT ON SEQUENCE {sequences} TO {role}'
        )


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging, unless the caller keeps its own
# (the app sets configure_logger=False when it migrates on startup).
if config.config_file_name is not None and config.attributes.get(
    'configure_logger', True
):
    fileConfig(config.config_file_name)

# Re-apply SQLAlchemy engine log suppression after fileConfig, which may override
# our earlier settings from alembic.ini. This ensures DEBUG-level SQL result logging
# is always suppressed, preventing sensitive data from leaking into log aggregators.
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine.Engine').setLevel(logging.WARNING)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    if CREATE_DATABASE_IF_MISSING:
        create_database_if_missing()

    with migration_engine() as engine, engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=target_metadata.schema,
        )

        # Lock number must be unique — md5 hash of 'openhands_enterprise_migrations'
        # Lock is released when the connection closes
        connection.execute(text('SELECT pg_advisory_lock(3617572382373537863)'))

        with context.begin_transaction():
            context.run_migrations()
            if TASK_QUEUE_DB_USER:
                grant_task_queue_role(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
