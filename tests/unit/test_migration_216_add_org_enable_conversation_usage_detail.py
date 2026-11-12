"""Real-DB test for migration 216: existing orgs read the opt-in as off."""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from storage.org import Org
from tests import postgres_testdb


def test_upgrade_leaves_existing_orgs_opted_out(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '214'
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

    for revision in ('215', '216'):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'upgrade', revision
        )

    with engine.connect() as conn:
        enabled = conn.execute(
            text('SELECT enable_conversation_usage_detail FROM org WHERE id = :id'),
            {'id': org_id},
        ).scalar_one()
    assert enabled is False


def test_new_org_created_through_the_orm_is_opted_out(
    engine: Engine, session_maker: sessionmaker
):
    org_id = uuid.uuid4()
    with session_maker() as session:
        session.add(Org(id=org_id, name=f'org_{org_id}'))
        session.commit()

    with engine.connect() as conn:
        enabled = conn.execute(
            text('SELECT enable_conversation_usage_detail FROM org WHERE id = :id'),
            {'id': org_id},
        ).scalar_one()
    assert enabled is False
