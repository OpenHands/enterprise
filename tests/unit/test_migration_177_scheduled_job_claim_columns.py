"""Revision 177 adds the claim columns the scheduled jobs' claims use."""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

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
