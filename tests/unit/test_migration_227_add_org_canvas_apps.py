"""Real-DB test for migration 227: Agent Canvas Apps approved for an organization."""

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from tests import postgres_testdb

APP_TABLES = {'org_canvas_app', 'org_member_canvas_app'}


def test_upgrade_adds_the_app_tables(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    # Arrange
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '226'
    )

    # Act
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '227'
    )

    # Assert
    assert APP_TABLES <= set(inspect(engine).get_table_names())


def test_downgrade_removes_what_upgrade_added(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '226'
    )

    assert not APP_TABLES & set(inspect(engine).get_table_names())
