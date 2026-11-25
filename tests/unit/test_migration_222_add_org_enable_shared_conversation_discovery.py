"""Real-DB test for migration 222: existing orgs keep discovery of shared conversations on."""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def test_upgrade_leaves_shared_conversation_discovery_on_for_existing_orgs(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    # Arrange
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '221'
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

    # Act
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '222'
    )

    # Assert
    with engine.connect() as conn:
        enabled = conn.execute(
            text('SELECT enable_shared_conversation_discovery FROM org WHERE id = :id'),
            {'id': org_id},
        ).scalar_one()
    assert enabled is True
