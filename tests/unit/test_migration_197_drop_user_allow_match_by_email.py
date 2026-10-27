"""Real-DB tests for migration 197 (drop ``allow_match_by_email`` from ``user``)."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '197_drop_user_allow_match_by_email.py'
)
spec = spec_from_file_location('migration_197', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_197 = module_from_spec(spec)
spec.loader.exec_module(migration_197)


def _run(engine: Engine, step: str) -> None:
    """Run ``migration_197.upgrade`` / ``downgrade`` alone, keeping the rest of
    the schema at head: later migrations add columns the ORM fixtures insert
    through (e.g. 199 ``org.meta_profiles``), which ``alembic downgrade 196``
    would remove."""
    with engine.begin() as conn:
        migration_197.op = Operations(MigrationContext.configure(conn))
        getattr(migration_197, step)()


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


def test_upgrade_drops_column(engine: Engine, create_user):
    _run(engine, 'downgrade')
    user = create_user(email='kept@example.com')
    with engine.begin() as conn:
        conn.execute(
            text('UPDATE "user" SET allow_match_by_email = true WHERE id = :id'),
            {'id': user.id},
        )

    _run(engine, 'upgrade')

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
