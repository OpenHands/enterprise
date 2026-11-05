"""Revision 174 installs exactly the Procrastinate 3.10.0 schema.

The revision runs vendored SQL rather than asking the installed library for
its schema, so these tests pin the two together: the vendored file is what
3.10.0 ships, and a migrated database is indistinguishable from one Procrastinate
set up itself.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import procrastinate
import psycopg
import psycopg.conninfo
import pytest
from procrastinate.schema import SchemaManager
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from tests import postgres_testdb

VENDORED = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'procrastinate'
    / '03.10.00_schema.sql'
)

CATALOG_QUERIES = {
    'columns': """
        SELECT table_name, column_name, data_type, column_default, is_nullable,
               ordinal_position
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name LIKE 'procrastinate%'
    """,
    'indexes': """
        SELECT indexname, indexdef FROM pg_indexes
         WHERE schemaname = 'public' AND tablename LIKE 'procrastinate%'
    """,
    'constraints': """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
          FROM pg_constraint WHERE conrelid::regclass::text LIKE 'procrastinate%'
    """,
    'functions': """
        SELECT proname, pg_get_functiondef(oid) FROM pg_proc
         WHERE proname LIKE 'procrastinate%'
    """,
    'triggers': """
        SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger
         WHERE tgname LIKE 'procrastinate%'
    """,
    'types': """
        SELECT t.typname, e.enumlabel, e.enumsortorder
          FROM pg_type t LEFT JOIN pg_enum e ON e.enumtypid = t.oid
         WHERE t.typname LIKE 'procrastinate%'
    """,
}


def _catalog(engine: Engine) -> dict[str, list[tuple]]:
    with engine.connect() as conn:
        return {
            name: sorted(tuple(row) for row in conn.execute(text(query)))
            for name, query in CATALOG_QUERIES.items()
        }


def _conninfo(server: postgres_testdb.PostgresServer, database: str, **kw) -> str:
    params = dict(
        host=server.host,
        port=server.port,
        dbname=database,
        user=server.user,
        password=server.password,
    )
    params.update(kw)
    return psycopg.conninfo.make_conninfo(**params)


@pytest.fixture
def fresh_database(
    postgres_server: postgres_testdb.PostgresServer,
) -> Iterator[str]:
    name = f'{postgres_testdb.TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}'
    postgres_testdb._run_admin_sql(
        postgres_server, f'CREATE DATABASE "{name}" TEMPLATE template0'
    )
    try:
        yield name
    finally:
        postgres_testdb.drop_test_database(postgres_server, name)


def test_vendored_schema_is_the_pinned_release():
    """Bumping Procrastinate must come with new vendored migration revisions."""
    assert procrastinate.__version__ == '3.10.0', (
        'Procrastinate was upgraded: vendor the new release migrations '
        '(procrastinate/sql/migrations/*_01_pre_*.sql and *_50_post_*.sql) as '
        'new Alembic revisions, then update this pin. Never edit revision 174.'
    )
    assert VENDORED.read_text() == SchemaManager.get_schema()


def test_migrated_schema_matches_a_fresh_procrastinate_install(
    engine: Engine,
    postgres_server: postgres_testdb.PostgresServer,
    fresh_database: str,
):
    with psycopg.connect(
        _conninfo(postgres_server, fresh_database), autocommit=True
    ) as conn:
        conn.execute(SchemaManager.get_schema())
    reference = create_engine(
        postgres_server.sync_url(fresh_database), poolclass=NullPool
    )
    try:
        expected = _catalog(reference)
    finally:
        reference.dispose()

    migrated = _catalog(engine)

    assert all(expected.values())
    assert migrated == expected


def test_downgrade_removes_every_procrastinate_object(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '173'
    )
    assert not any(_catalog(engine).values())

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert all(_catalog(engine).values())
