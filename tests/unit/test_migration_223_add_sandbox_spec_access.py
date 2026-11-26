"""Real-DB test for migration 223: private sandbox specs and org grants."""

import uuid

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def test_upgrade_creates_access_tables_and_leaves_existing_orgs_without_a_default(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    # Arrange
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    org_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO org (id, name, status, enable_proactive_conversation_starters,'
                ' org_version, agent_settings, conversation_settings,'
                ' byor_export_enabled, is_default)'
                " VALUES (:id, :id, 'active', true, 0, '{}', '{}', false, false)"
            ),
            {'id': org_id},
        )
    assert 'sandbox_spec_access' not in inspect(engine).get_table_names()

    # Act
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '223'
    )

    # Assert
    tables = inspect(engine).get_table_names()
    assert {'sandbox_spec_access', 'sandbox_spec_org_grant'} <= set(tables)
    with engine.connect() as conn:
        default = conn.execute(
            text('SELECT default_sandbox_spec_name FROM org WHERE id = :id'),
            {'id': org_id},
        ).scalar_one()
        private_specs = conn.execute(
            text('SELECT count(*) FROM sandbox_spec_access')
        ).scalar_one()
    assert default is None
    assert private_specs == 0


def test_downgrade_removes_what_upgrade_added(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )

    inspector = inspect(engine)
    assert 'sandbox_spec_access' not in inspector.get_table_names()
    assert 'sandbox_spec_org_grant' not in inspector.get_table_names()
    assert 'default_sandbox_spec_name' not in {
        c['name'] for c in inspector.get_columns('org')
    }
