"""Tests for the Postgres test-database harness in ``tests/postgres_testdb``."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from storage.org import Org
from storage.role import Role
from tests import postgres_testdb


@pytest.fixture
def pg_engine(test_database: postgres_testdb.TestDatabase) -> Iterator[Engine]:
    """Sync engine on this test's database, on the driver production uses."""
    engine = create_engine(test_database.sync_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def _table_names(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    'ORDER BY tablename'
                )
            ).scalars()
        )


def test_schema_is_at_migration_head(pg_engine: Engine):
    """The template is built by ``alembic upgrade head``, not ``create_all``."""
    config = Config(str(postgres_testdb.ALEMBIC_INI))
    config.set_main_option('script_location', str(postgres_testdb.MIGRATIONS_DIR))
    expected = set(ScriptDirectory.from_config(config).get_heads())

    with pg_engine.connect() as conn:
        applied = set(
            conn.execute(text('SELECT version_num FROM alembic_version')).scalars()
        )

    assert applied == expected


def test_every_table_starts_empty(pg_engine: Engine):
    """Including the reference rows migrations 089 and 098 seed."""
    tables = _table_names(pg_engine)
    assert 'role' in tables and 'verified_models' in tables

    with pg_engine.connect() as conn:
        non_empty = [
            table
            for table in tables
            if table != 'alembic_version'
            and conn.execute(text(f'SELECT 1 FROM "{table}" LIMIT 1')).scalar()
            is not None
        ]

    assert non_empty == []


def test_identity_sequences_start_at_one(pg_engine: Engine):
    """``TRUNCATE ... RESTART IDENTITY`` leaves generated ids predictable."""
    with sessionmaker(bind=pg_engine)() as session:
        role = Role(name='first', rank=1)
        session.add(role)
        session.commit()
        assert role.id == 1


def test_writes_do_not_leak_into_other_databases(
    pg_engine: Engine, postgres_server, postgres_template
):
    """A clone of the template never sees what this test wrote."""
    with sessionmaker(bind=pg_engine)() as session:
        session.add(Org(id=uuid.uuid4(), name='leak-check', org_version=0))
        session.commit()

    other = postgres_testdb.create_test_database(postgres_server, postgres_template)
    other_engine = create_engine(postgres_server.sync_url(other), poolclass=NullPool)
    try:
        with other_engine.connect() as conn:
            assert conn.execute(text('SELECT count(*) FROM org')).scalar() == 0
    finally:
        other_engine.dispose()
        postgres_testdb.drop_test_database(postgres_server, other)


def test_foreign_keys_are_enforced(pg_engine: Engine):
    """The migrated schema brings its constraints with it."""
    # DatabaseError rather than IntegrityError: pg8000 reports a foreign key
    # violation as a ProgrammingError, where asyncpg reports IntegrityError.
    with pytest.raises(DatabaseError), pg_engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO "user" (id, current_org_id, is_disabled) '
                'VALUES (:id, :org_id, false)'
            ),
            {'id': str(uuid.uuid4()), 'org_id': str(uuid.uuid4())},
        )
