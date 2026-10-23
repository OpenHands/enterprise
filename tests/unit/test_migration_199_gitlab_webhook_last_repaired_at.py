"""Revision 199 adds gitlab_webhook.last_repaired_at for the repair sweep."""

from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from storage.gitlab_webhook import GitlabWebhook
from tests import postgres_testdb


def _column(engine: Engine):
    columns = inspect(engine).get_columns('gitlab_webhook')
    return next((c for c in columns if c['name'] == 'last_repaired_at'), None)


def test_model_maps_the_migrated_column(engine: Engine):
    dialect = postgresql.dialect()
    migrated = _column(engine)
    model = GitlabWebhook.__table__.c['last_repaired_at']

    assert migrated is not None
    assert model.type.compile(dialect) == migrated['type'].compile(dialect)
    assert migrated['nullable'] and model.nullable
    indexes = {i['name'] for i in inspect(engine).get_indexes('gitlab_webhook')}
    assert 'ix_gitlab_webhook_last_repaired_at' in indexes


def test_downgrade_drops_the_column_and_upgrade_restores_it(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '198'
    )
    assert _column(engine) is None

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert _column(engine) is not None
