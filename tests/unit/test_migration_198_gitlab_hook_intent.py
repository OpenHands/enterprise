"""Revision 198 adds gitlab_hook_intent and backfills the current identities."""

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from storage.gitlab_hook_intent import GitlabHookIntent
from tests import postgres_testdb


def test_model_maps_the_migrated_table(engine: Engine):
    dialect = postgresql.dialect()
    inspector = inspect(engine)
    migrated = {c['name']: c for c in inspector.get_columns('gitlab_hook_intent')}
    table = GitlabHookIntent.__table__

    assert set(migrated) == set(table.c.keys())
    for name, column in migrated.items():
        assert table.c[name].type.compile(dialect) == column['type'].compile(dialect)
        assert table.c[name].nullable == column['nullable']
    indexes = {i['name']: i for i in inspector.get_indexes('gitlab_hook_intent')}
    assert indexes['uq_gitlab_hook_intent_committed']['unique']


def test_upgrade_gives_every_existing_identity_a_committed_intent(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '197'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO gitlab_webhook (project_id, user_id, webhook_exists, '
                'webhook_uuid) VALUES '
                "('p-1', 'user-1', true, 'uuid-1'), "
                "('p-2', 'user-1', false, NULL)"
            )
        )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )

    with engine.connect() as conn:
        intents = conn.execute(
            text(
                'SELECT i.uuid, w.project_id, i.state, i.run_id '
                'FROM gitlab_hook_intent i JOIN gitlab_webhook w '
                'ON w.id = i.webhook_id'
            )
        ).all()
    assert [tuple(row) for row in intents] == [('uuid-1', 'p-1', 'committed', None)]


def test_downgrade_drops_the_table(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '197'
    )

    assert not inspect(engine).has_table('gitlab_hook_intent')
