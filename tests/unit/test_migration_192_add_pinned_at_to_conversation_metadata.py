"""Real-DB tests for migration 192 (``pinned_at`` on ``conversation_metadata``)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _insert_conversation(engine: Engine, conversation_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO conversation_metadata (conversation_id, created_at, last_updated_at) '
                'VALUES (:conversation_id, now(), now())'
            ),
            {'conversation_id': conversation_id},
        )


def _pinned_at(engine: Engine, conversation_id: str) -> object:
    with engine.connect() as conn:
        return conn.execute(
            text(
                'SELECT pinned_at FROM conversation_metadata '
                'WHERE conversation_id = :conversation_id'
            ),
            {'conversation_id': conversation_id},
        ).scalar_one()


def test_upgrade_leaves_existing_conversations_unpinned(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '191'
    )
    _insert_conversation(engine, 'existing')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '192'
    )

    assert _pinned_at(engine, 'existing') is None


def test_new_rows_are_unpinned(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '192'
    )

    _insert_conversation(engine, 'new')

    assert _pinned_at(engine, 'new') is None


def test_downgrade_drops_column(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '192'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '191'
    )

    with engine.connect() as conn:
        columns = set(
            conn.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    "WHERE table_name = 'conversation_metadata'"
                )
            ).scalars()
        )

    assert 'pinned_at' not in columns
