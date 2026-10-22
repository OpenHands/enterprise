"""Real-DB tests for migration 195 (``enabled`` on ``auth_providers``)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _insert_provider(engine: Engine, enabled: bool | None = None) -> int:
    columns = 'kind, title, is_idp, details'
    values = "'PasswordProviderDetails', 'Integrated IDP', true, :details"
    params: dict = {'details': json.dumps({'kind': 'PasswordProviderDetails'})}
    if enabled is not None:
        columns += ', enabled'
        values += ', :enabled'
        params['enabled'] = enabled
    with engine.begin() as conn:
        return conn.execute(
            text(
                f'INSERT INTO auth_providers ({columns}) VALUES ({values}) RETURNING id'
            ),
            params,
        ).scalar_one()


def _enabled(engine: Engine, provider_id: int) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            text('SELECT enabled FROM auth_providers WHERE id = :id'),
            {'id': provider_id},
        ).scalar_one()


def test_upgrade_backfills_existing_rows_to_true(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """Every row that existed before the column did was reachable, so an
    upgrade must not silently disable configured login IDPs."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '194'
    )
    provider_id = _insert_provider(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '195'
    )

    assert _enabled(engine, provider_id) is True


def test_upgrade_defaults_new_rows_to_false(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '195'
    )

    provider_id = _insert_provider(engine)

    assert _enabled(engine, provider_id) is False


def test_upgrade_allows_explicit_true_on_new_rows(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '195'
    )

    provider_id = _insert_provider(engine, enabled=True)

    assert _enabled(engine, provider_id) is True


def test_downgrade_drops_column(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '195'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '194'
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
    assert 'enabled' not in columns
