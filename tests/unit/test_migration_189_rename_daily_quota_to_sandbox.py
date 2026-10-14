"""Real-DB tests for migration 189 (daily conversation quota -> sandbox).

A table rename plus three column renames, so these tests assert the renames
round-trip: history rows and limit overrides survive under the new names, and
the names match what the models (``storage.daily_sandbox_usage``,
``storage.user.User.daily_sandbox_limit``, ``storage.org.Org``) expect.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _columns(engine: Engine, table_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT column_name FROM information_schema.columns '
                'WHERE table_name = :table'
            ),
            {'table': table_name},
        ).scalars()
        return set(rows)


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT tablename FROM pg_tables WHERE schemaname = :schema'),
            {'schema': 'public'},
        ).scalars()
        return set(rows)


def _insert_user_and_org(engine: Engine) -> tuple[str, str]:
    """Insert a user + personal org with raw SQL (ORM models carry the
    post-rename columns, which do not exist at revision 188)."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO org (id, name, org_version, agent_settings, '
                "conversation_settings) VALUES (:id, :name, 10, '{}', '{}')"
            ),
            {'id': org_id, 'name': f'test-org-{org_id[:8]}'},
        )
        conn.execute(
            text('INSERT INTO "user" (id, current_org_id) VALUES (:id, :org_id)'),
            {'id': user_id, 'org_id': org_id},
        )
    return user_id, org_id


def test_upgrade_renames_tables_and_columns_preserving_history(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    user_id, org_id = _insert_user_and_org(engine)
    user = type('U', (), {'id': user_id, 'current_org_id': org_id})()
    with engine.begin() as conn:
        conn.execute(
            text('UPDATE "user" SET daily_conversation_limit = 7 WHERE id = :id'),
            {'id': user.id},
        )
        conn.execute(
            text('UPDATE org SET daily_conversation_limit = -1 WHERE id = :id'),
            {'id': user.current_org_id},
        )
        conn.execute(
            text(
                'INSERT INTO daily_conversation_usage '
                '(user_id, usage_date, conversation_count, created_at, updated_at) '
                'VALUES (:user_id, :usage_date, 12, :now, :now)'
            ),
            {
                'user_id': user.id,
                'usage_date': date(2026, 10, 14),
                'now': datetime(2026, 10, 14, tzinfo=timezone.utc),
            },
        )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    assert 'daily_sandbox_usage' in _tables(engine)
    assert 'daily_conversation_usage' not in _tables(engine)
    assert _columns(engine, 'daily_sandbox_usage') >= {
        'user_id',
        'usage_date',
        'sandbox_count',
    }
    assert 'conversation_count' not in _columns(engine, 'daily_sandbox_usage')
    assert 'daily_sandbox_limit' in _columns(engine, 'user')
    assert 'daily_sandbox_limit' in _columns(engine, 'org')

    with engine.connect() as conn:
        usage = conn.execute(
            text(
                'SELECT sandbox_count FROM daily_sandbox_usage WHERE user_id = :user_id'
            ),
            {'user_id': user.id},
        ).scalar_one()
        user_limit = conn.execute(
            text('SELECT daily_sandbox_limit FROM "user" WHERE id = :id'),
            {'id': user.id},
        ).scalar_one()
        org_limit = conn.execute(
            text('SELECT daily_sandbox_limit FROM org WHERE id = :id'),
            {'id': user.current_org_id},
        ).scalar_one()
    assert usage == 12
    assert user_limit == 7
    assert org_limit == -1


def test_downgrade_restores_conversation_names(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    user_id, _org_id = _insert_user_and_org(engine)
    user = type('U', (), {'id': user_id})()
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO daily_conversation_usage '
                '(user_id, usage_date, conversation_count, created_at, updated_at) '
                'VALUES (:user_id, :usage_date, 3, :now, :now)'
            ),
            {
                'user_id': user.id,
                'usage_date': date(2026, 10, 14),
                'now': datetime(2026, 10, 14, tzinfo=timezone.utc),
            },
        )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    assert 'daily_conversation_usage' in _tables(engine)
    assert 'conversation_count' in _columns(engine, 'daily_conversation_usage')
    assert 'daily_conversation_limit' in _columns(engine, 'user')
    assert 'daily_conversation_limit' in _columns(engine, 'org')
    with engine.connect() as conn:
        count = conn.execute(
            text(
                'SELECT conversation_count FROM daily_conversation_usage '
                'WHERE user_id = :user_id'
            ),
            {'user_id': user.id},
        ).scalar_one()
    assert count == 3
