"""Verify that the settings cutover copies preferences by org and leaves secrets alone."""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def test_round_trip_preserves_org_ownership_and_credentials(
    engine: Engine, test_database
):
    # On a reconciled graph, the immediately preceding revision is the first
    # down-revision of this cutover, not necessarily the original 224.
    def migrate(direction, target):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, direction, target
        )

    migrate('downgrade', '-1')
    ids = [uuid.uuid4(), uuid.uuid4()]
    with engine.begin() as conn:
        for idx, org_id in enumerate(ids):
            conn.execute(
                text("""INSERT INTO org (id, name, status, org_version,
                    agent_settings, conversation_settings, meta_profiles,
                    byor_export_enabled, is_default, _llm_api_key)
                    VALUES (:id, :name, 'active', 0, '{}', CAST(:conversation AS json),
                    CAST(:router AS json), false, false, :credential)"""),
                dict(
                    id=org_id,
                    name=str(org_id),
                    conversation=f'{{"max_iterations":{40 + idx}}}',
                    router='{"profiles":{},"active":null}' if idx else None,
                    credential=f'opaque-ciphertext-{idx}',
                ),
            )
    migrate('upgrade', 'head')
    with engine.connect() as conn:
        for idx, org_id in enumerate(ids):
            document, credential = conn.execute(
                text('SELECT settings, _llm_api_key FROM org WHERE id = :id'),
                {'id': org_id},
            ).one()
            assert document['conversation_settings'] == {'max_iterations': 40 + idx}
            assert document['meta_profiles'] == (
                {'profiles': {}, 'active': None} if idx else None
            )
            assert '_llm_api_key' not in document
            assert credential == f'opaque-ciphertext-{idx}'
    migrate('downgrade', '-1')
    with engine.connect() as conn:
        for idx, org_id in enumerate(ids):
            conversation, router, credential = conn.execute(
                text(
                    'SELECT conversation_settings, meta_profiles, _llm_api_key FROM org WHERE id = :id'
                ),
                {'id': org_id},
            ).one()
            assert conversation == {'max_iterations': 40 + idx}
            assert router == ({'profiles': {}, 'active': None} if idx else None)
            assert credential == f'opaque-ciphertext-{idx}'
