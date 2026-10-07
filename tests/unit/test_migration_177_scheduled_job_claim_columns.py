"""Revision 177 adds the claim columns the scheduled jobs' claims use."""

import uuid

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from storage.gitlab_webhook import GitlabWebhook
from storage.maintenance_task import MaintenanceTask
from tests import postgres_testdb

NEW_COLUMNS = {
    ('maintenance_tasks', 'claim_run_id', 'uuid', 'YES', None),
    ('maintenance_tasks', 'claimed_at', 'timestamp with time zone', 'YES', None),
    ('gitlab_webhook', 'claim_run_id', 'uuid', 'YES', None),
    ('gitlab_webhook', 'claimed_at', 'timestamp with time zone', 'YES', None),
    ('gitlab_webhook', 'reinstall_requested_gen', 'integer', 'NO', '0'),
    ('gitlab_webhook', 'reinstall_done_gen', 'integer', 'NO', '0'),
}


def _new_columns(engine: Engine) -> set[tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT table_name, column_name, data_type, is_nullable, '
                'column_default FROM information_schema.columns '
                "WHERE table_schema = 'public' AND table_name IN "
                "('maintenance_tasks', 'gitlab_webhook')"
            )
        )
        columns = {tuple(row) for row in rows}
    names = {column[:2] for column in NEW_COLUMNS}
    return {column for column in columns if column[:2] in names}


def test_upgrade_adds_the_claim_columns(engine: Engine):
    assert _new_columns(engine) == NEW_COLUMNS


def test_existing_webhook_rows_need_no_reinstall(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '176'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO gitlab_webhook (project_id, user_id, webhook_exists) '
                "VALUES (:project, 'user-1', true)"
            ),
            {'project': uuid.uuid4().hex},
        )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )

    with engine.connect() as conn:
        row = conn.execute(
            text(
                'SELECT claim_run_id, claimed_at, reinstall_requested_gen, '
                'reinstall_done_gen FROM gitlab_webhook'
            )
        ).one()
    assert tuple(row) == (None, None, 0, 0)


def test_a_webhook_row_added_through_the_model_needs_no_reinstall(
    session_maker: sessionmaker,
):
    with session_maker() as session:
        session.add(
            GitlabWebhook(project_id='p-1', user_id='user-1', webhook_exists=True)
        )
        session.commit()
        row = session.scalars(select(GitlabWebhook)).one()
        assert (row.reinstall_requested_gen, row.reinstall_done_gen) == (0, 0)


def test_models_map_the_migrated_column_types(engine: Engine):
    dialect = postgresql.dialect()
    inspector = inspect(engine)
    for model in (MaintenanceTask, GitlabWebhook):
        table = model.__table__
        migrated = {c['name']: c for c in inspector.get_columns(table.name)}
        for table_name, column_name, *_ in NEW_COLUMNS:
            if table_name != table.name:
                continue
            model_type = table.c[column_name].type.compile(dialect)
            migrated_type = migrated[column_name]['type'].compile(dialect)
            assert model_type == migrated_type, (table_name, column_name)


def test_downgrade_removes_the_columns_and_upgrade_restores_them(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '176'
    )
    assert _new_columns(engine) == set()

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert _new_columns(engine) == NEW_COLUMNS
