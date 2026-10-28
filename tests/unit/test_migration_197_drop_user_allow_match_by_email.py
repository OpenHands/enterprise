"""Real-DB tests for migration 197 (drop ``allow_match_by_email`` from ``user``)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _user_columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    "WHERE table_name = 'user'"
                )
            ).scalars()
        )


def test_upgrade_drops_column(
    engine: Engine, test_database: postgres_testdb.TestDatabase, create_user
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '196'
    )
    user = create_user(email='kept@example.com')
    with engine.begin() as conn:
        conn.execute(
            text('UPDATE "user" SET allow_match_by_email = true WHERE id = :id'),
            {'id': user.id},
        )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '197'
    )

    assert 'allow_match_by_email' not in _user_columns(engine)
    with engine.connect() as conn:
        assert (
            conn.execute(
                text('SELECT email FROM "user" WHERE id = :id'), {'id': user.id}
            ).scalar_one()
            == 'kept@example.com'
        )


def test_downgrade_restores_column_as_false(
    engine: Engine, test_database: postgres_testdb.TestDatabase, create_user
):
    user = create_user(email='restored@example.com')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '196'
    )

    with engine.connect() as conn:
        assert (
            conn.execute(
                text('SELECT allow_match_by_email FROM "user" WHERE id = :id'),
                {'id': user.id},
            ).scalar_one()
            is False
        )
