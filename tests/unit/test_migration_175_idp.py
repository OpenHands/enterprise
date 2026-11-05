"""Revision 175 adds ``user.password_hash`` for the dev IDP password login.

The dev IDP is an in-memory sentinel (not a row in ``oauth_providers``), so
this migration only adds the ``password_hash`` column. These tests run against
a freshly-migrated database rather than the shared ``test_database`` fixture,
because the per-test template has its seeded rows truncated (see
``tests.postgres_testdb._empty_tables``) so other tests get a blank slate.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from tests import postgres_testdb


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


@pytest.fixture
def fresh_engine(
    postgres_server: postgres_testdb.PostgresServer, fresh_database: str
) -> Iterator[Engine]:
    engine = create_engine(postgres_server.sync_url(fresh_database), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_adds_password_hash_column(
    postgres_server: postgres_testdb.PostgresServer,
    fresh_database: str,
    fresh_engine: Engine,
):
    postgres_testdb.run_alembic(postgres_server, fresh_database, 'upgrade', 'head')
    with fresh_engine.connect() as conn:
        row = conn.execute(
            text(
                'SELECT data_type, is_nullable FROM information_schema.columns '
                "WHERE table_name = 'user' AND column_name = 'password_hash'"
            )
        ).one()
    assert row.data_type == 'character varying'
    assert row.is_nullable == 'YES'


def test_downgrade_removes_password_hash_column(
    postgres_server: postgres_testdb.PostgresServer,
    fresh_database: str,
    fresh_engine: Engine,
):
    postgres_testdb.run_alembic(postgres_server, fresh_database, 'upgrade', 'head')
    postgres_testdb.run_alembic(postgres_server, fresh_database, 'downgrade', '174')

    with fresh_engine.connect() as conn:
        column_count = conn.execute(
            text(
                'SELECT count(*) FROM information_schema.columns '
                "WHERE table_name = 'user' AND column_name = 'password_hash'"
            )
        ).scalar_one()
    assert column_count == 0
