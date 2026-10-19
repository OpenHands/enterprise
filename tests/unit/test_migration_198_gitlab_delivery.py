"""Revision 198 adds gitlab_delivery, the GitLab event handler's dedupe table."""

from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from storage.gitlab_delivery import GitlabDelivery
from tests import postgres_testdb


def _has_table(engine: Engine) -> bool:
    return inspect(engine).has_table('gitlab_delivery')


def test_model_maps_the_migrated_table(engine: Engine):
    dialect = postgresql.dialect()
    inspector = inspect(engine)
    migrated = {c['name']: c for c in inspector.get_columns('gitlab_delivery')}
    table = GitlabDelivery.__table__

    assert set(migrated) == set(table.c.keys())
    for name, column in migrated.items():
        assert table.c[name].type.compile(dialect) == column['type'].compile(dialect)
        assert table.c[name].nullable == column['nullable']
    assert inspector.get_pk_constraint('gitlab_delivery')['constrained_columns'] == [
        'body_sha256'
    ]
    assert {i['name'] for i in inspector.get_indexes('gitlab_delivery')} == {
        'ix_gitlab_delivery_received_at'
    }


def test_downgrade_drops_the_table_and_upgrade_restores_it(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '197'
    )
    assert not _has_table(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert _has_table(engine)
