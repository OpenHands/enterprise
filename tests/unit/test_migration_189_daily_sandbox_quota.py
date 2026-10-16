"""Real-DB tests for migration 189 (daily sandbox quota).

Migration 189 is an intentional no-op: the quota's Python-side names change
to sandbox (``DailySandboxUsage``, ``sandbox_count``, ``daily_sandbox_limit``)
but the physical schema keeps its legacy names so pods on the previous image
keep working while a rolling deploy runs ``alembic upgrade head`` from the
new pods' init containers (and a rollback needs no manual downgrade).

These tests pin that contract: upgrading 188 -> 189 and downgrading back
leave tables, columns, history rows, and limit overrides exactly as they
were, under the legacy physical names the models map.
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


def _seed_legacy_quota(engine: Engine, user_id: str, org_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text('UPDATE "user" SET daily_conversation_limit = 7 WHERE id = :id'),
            {'id': user_id},
        )
        conn.execute(
            text('UPDATE org SET daily_conversation_limit = -1 WHERE id = :id'),
            {'id': org_id},
        )
        conn.execute(
            text(
                'INSERT INTO daily_conversation_usage '
                '(user_id, usage_date, conversation_count, created_at, updated_at) '
                'VALUES (:user_id, :usage_date, 12, :now, :now)'
            ),
            {
                'user_id': user_id,
                'usage_date': date(2026, 10, 14),
                'now': datetime(2026, 10, 14, tzinfo=timezone.utc),
            },
        )


def _assert_legacy_schema_and_data(engine: Engine, user_id: str, org_id: str):
    assert 'daily_conversation_usage' in _tables(engine)
    assert 'daily_sandbox_usage' not in _tables(engine)
    assert _columns(engine, 'daily_conversation_usage') >= {
        'user_id',
        'usage_date',
        'conversation_count',
    }
    assert 'sandbox_count' not in _columns(engine, 'daily_conversation_usage')
    assert 'daily_conversation_limit' in _columns(engine, 'user')
    assert 'daily_conversation_limit' in _columns(engine, 'org')
    assert 'daily_sandbox_limit' not in _columns(engine, 'user')
    assert 'daily_sandbox_limit' not in _columns(engine, 'org')

    with engine.connect() as conn:
        usage = conn.execute(
            text(
                'SELECT conversation_count FROM daily_conversation_usage '
                'WHERE user_id = :user_id'
            ),
            {'user_id': user_id},
        ).scalar_one()
        user_limit = conn.execute(
            text('SELECT daily_conversation_limit FROM "user" WHERE id = :id'),
            {'id': user_id},
        ).scalar_one()
        org_limit = conn.execute(
            text('SELECT daily_conversation_limit FROM org WHERE id = :id'),
            {'id': org_id},
        ).scalar_one()
    assert usage == 12
    assert user_limit == 7
    assert org_limit == -1


def test_upgrade_189_keeps_legacy_names_and_data(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    user_id, org_id = _insert_user_and_org(engine)
    _seed_legacy_quota(engine, user_id, org_id)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    _assert_legacy_schema_and_data(engine, user_id, org_id)


def test_downgrade_189_keeps_legacy_names_and_data(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    user_id, org_id = _insert_user_and_org(engine)
    _seed_legacy_quota(engine, user_id, org_id)
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    _assert_legacy_schema_and_data(engine, user_id, org_id)
