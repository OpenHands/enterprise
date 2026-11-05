"""Real-DB tests for migration 191 (``allow_match_by_email`` on
``auth_providers``)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _insert_provider(engine: Engine, allow_match_by_email: bool | None = None) -> int:
    columns = 'kind, title, is_idp, details'
    values = "'PasswordProviderDetails', 'Integrated IDP', true, :details"
    params: dict = {'details': json.dumps({'kind': 'PasswordProviderDetails'})}
    if allow_match_by_email is not None:
        columns += ', allow_match_by_email'
        values += ', :allow_match_by_email'
        params['allow_match_by_email'] = allow_match_by_email
    with engine.begin() as conn:
        return conn.execute(
            text(
                f'INSERT INTO auth_providers ({columns}) VALUES ({values}) RETURNING id'
            ),
            params,
        ).scalar_one()


def _allow_match_by_email(engine: Engine, provider_id: int) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            text('SELECT allow_match_by_email FROM auth_providers WHERE id = :id'),
            {'id': provider_id},
        ).scalar_one()


def test_upgrade_backfills_existing_rows_to_true(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '190'
    )
    provider_id = _insert_provider(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '191'
    )

    assert _allow_match_by_email(engine, provider_id) is True


def test_upgrade_allows_explicit_false_on_new_rows(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '191'
    )

    provider_id = _insert_provider(engine, allow_match_by_email=False)

    assert _allow_match_by_email(engine, provider_id) is False


def test_downgrade_drops_column(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '191'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '190'
    )

    with engine.connect() as conn:
        columns = set(
            conn.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    "WHERE table_name = 'auth_providers'"
                )
            ).scalars()
        )
    assert 'allow_match_by_email' not in columns
