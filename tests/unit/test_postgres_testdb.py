"""Tests for the Postgres test-database fixtures in ``tests/postgres_testdb``."""

from __future__ import annotations

import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError
from sqlalchemy.pool import NullPool

from tests import postgres_testdb


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


def test_schema_is_at_migration_head(engine: Engine):
    """The template is built by ``alembic upgrade head``, not ``create_all``."""
    config = Config(str(postgres_testdb.ALEMBIC_INI))
    config.set_main_option('script_location', str(postgres_testdb.MIGRATIONS_DIR))
    expected = set(ScriptDirectory.from_config(config).get_heads())

    with engine.connect() as conn:
        applied = set(
            conn.execute(text('SELECT version_num FROM alembic_version')).scalars()
        )

    assert applied == expected


def test_every_table_starts_empty(engine: Engine):
    """Including the reference rows migrations 089 and 098 seed."""
    tables = _table_names(engine)
    assert 'role' in tables and 'verified_models' in tables

    with engine.connect() as conn:
        non_empty = [
            table
            for table in tables
            if table != 'alembic_version'
            and conn.execute(text(f'SELECT 1 FROM "{table}" LIMIT 1')).scalar()
            is not None
        ]

    assert non_empty == []


def test_identity_sequences_start_at_one(session_maker):
    """``TRUNCATE ... RESTART IDENTITY`` leaves generated ids predictable."""
    from storage.role import Role

    with session_maker() as session:
        role = Role(name='first', rank=1)
        session.add(role)
        session.commit()
        assert role.id == 1


def test_writes_do_not_leak_into_other_databases(
    engine: Engine, create_org, postgres_server, postgres_template
):
    """A clone of the template never sees what this test wrote."""
    create_org(name='leak-check')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM org')).scalar() == 1

    other = postgres_testdb.create_test_database(postgres_server, postgres_template)
    other_engine = create_engine(postgres_server.sync_url(other), poolclass=NullPool)
    try:
        with other_engine.connect() as conn:
            assert conn.execute(text('SELECT count(*) FROM org')).scalar() == 0
    finally:
        other_engine.dispose()
        postgres_testdb.drop_test_database(postgres_server, other)


def test_foreign_keys_are_enforced(engine: Engine):
    """The migrated schema brings its constraints with it."""
    # DatabaseError rather than IntegrityError: pg8000 reports a foreign key
    # violation as a ProgrammingError, where asyncpg reports IntegrityError.
    with pytest.raises(DatabaseError), engine.begin() as conn:
        conn.execute(
            text('INSERT INTO "user" (id, current_org_id) VALUES (:id, :org_id)'),
            {'id': str(uuid.uuid4()), 'org_id': str(uuid.uuid4())},
        )
