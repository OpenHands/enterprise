"""Verify both Runtime API migration boundaries on disposable PostgreSQL."""

from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from openhands.app_server.services.db_session_injector import DbSessionInjector
from tests import postgres_testdb
from tests.postgres_testdb import PostgresServer

ROOT = Path(__file__).resolve().parents[3]


def test_enterprise_working_directory_roundtrip_preserves_remote_rows(
    engine: Engine,
) -> None:
    config = Config()
    config.set_main_option('script_location', str(ROOT / 'migrations'))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision('167')
    assert revision is not None
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO v1_remote_sandbox (id, sandbox_spec_id, created_by_user_id) VALUES ('historical', 'native-image', 'alice')"
            )
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.module.downgrade()
        assert 'working_dir' not in {
            column['name']
            for column in inspect(connection).get_columns('v1_remote_sandbox')
        }
        assert (
            connection.scalar(
                text(
                    "SELECT sandbox_spec_id FROM v1_remote_sandbox WHERE id='historical'"
                )
            )
            == 'native-image'
        )
        with Operations.context(context):
            revision.module.upgrade()
        assert connection.execute(
            text(
                "SELECT sandbox_spec_id, working_dir FROM v1_remote_sandbox WHERE id='historical'"
            )
        ).one() == ('native-image', None)


def test_standalone_working_directory_migration_chain(
    postgres_server: PostgresServer,
) -> None:
    name = postgres_testdb.create_test_database(postgres_server, 'template0')
    engine = create_engine(postgres_server.sync_url(name), poolclass=NullPool)
    config = Config()
    config.set_main_option(
        'script_location', str(ROOT / 'openhands/app_server/app_lifespan/alembic')
    )
    try:
        with patch.object(DbSessionInjector, 'get_db_engine', return_value=engine):
            command.upgrade(config, '014')
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO v1_remote_sandbox (id, sandbox_spec_id, created_by_user_id) VALUES ('historical', 'native-image', 'alice')"
                    )
                )
            command.upgrade(config, '015')
            with engine.connect() as connection:
                assert (
                    connection.scalar(text('SELECT version_num FROM alembic_version'))
                    == '015'
                )
                assert connection.execute(
                    text(
                        "SELECT sandbox_spec_id, working_dir FROM v1_remote_sandbox WHERE id='historical'"
                    )
                ).one() == ('native-image', None)
                assert 'v1_managed_sandbox' not in inspect(connection).get_table_names()
            command.downgrade(config, '014')
            with engine.connect() as connection:
                assert 'working_dir' not in {
                    column['name']
                    for column in inspect(connection).get_columns('v1_remote_sandbox')
                }
                assert (
                    connection.scalar(
                        text(
                            "SELECT sandbox_spec_id FROM v1_remote_sandbox WHERE id='historical'"
                        )
                    )
                    == 'native-image'
                )
            command.upgrade(config, '015')
    finally:
        engine.dispose()
        postgres_testdb.drop_test_database(postgres_server, name)
