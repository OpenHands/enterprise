"""Alembic environment configuration for integrations-hub migrations.

Hub shares the OpenHands Enterprise Postgres database (``DB_*`` / optional
``INTHUB_POSTGRES_URL`` override). Version history is stored in a dedicated
table so it does not collide with enterprise SaaS Alembic.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context


# Alembic Config object for access to .ini file values
config = context.config

# Keep Hub revision state separate from enterprise/migrations.
HUB_VERSION_TABLE = 'alembic_version_integrations_hub'

# Set up Python logging from the config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_database_url() -> str:
    """Resolve the shared OHE Postgres URL for Hub Alembic migrations.

    Prefers ``INTHUB_POSTGRES_URL``, otherwise builds from enterprise ``DB_*``
    env vars so Hub shares the same database as the rest of OpenHands Enterprise.
    Converts to ``postgresql+psycopg://`` for SQLAlchemy with the psycopg3 driver.
    """
    from integrations_hub.mount import resolve_shared_postgres_url

    url = resolve_shared_postgres_url()
    if not url:
        raise RuntimeError(
            'Database URL not configured. Set DB_HOST (shared OHE Postgres) '
            'or INTHUB_POSTGRES_URL.'
        )
    # Convert to SQLAlchemy URL with psycopg3 driver
    # postgres:// or postgresql:// -> postgresql+psycopg://
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This generates SQL scripts without connecting to the database.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        version_table=HUB_VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates a connection to the database and runs migrations.
    """
    url = get_database_url()

    # Create engine with NullPool for serverless compatibility
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table=HUB_VERSION_TABLE,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
