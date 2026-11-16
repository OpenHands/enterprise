from sqlalchemy import inspect

from tests import postgres_testdb


def test_startup_title_columns_upgrade_and_downgrade(engine, test_database):
    for direction, revision, expected in (
        ('downgrade', '206', False),
        ('upgrade', '207', True),
        ('downgrade', '206', False),
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, direction, revision
        )
        for table in ('conversation_metadata', 'app_conversation_start_task'):
            columns = {column['name'] for column in inspect(engine).get_columns(table)}
            assert ('title_generation' in columns) is expected
