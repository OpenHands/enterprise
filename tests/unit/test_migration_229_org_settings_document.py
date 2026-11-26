"""Verify that the settings cutover copies preferences by org and leaves secrets alone."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def test_round_trip_preserves_org_ownership_and_credentials(
    engine: Engine, test_database
):
    def migrate(direction, target):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, direction, target
        )

    migrate('downgrade', '226')
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
    migrate('downgrade', '226')
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


@pytest.mark.parametrize('old_stamp', ['223', '224'])
def test_upgrade_reconciles_colliding_sandbox_stamp(engine, test_database, old_stamp):
    def migrate(*args):
        postgres_testdb.run_alembic(test_database.server, test_database.name, *args)

    # Build the sandbox branch's schema, but restore the exact seeded OAuth
    # scopes that a database on that branch would still have.
    migrate('downgrade', '226' if old_stamp == '224' else '225')
    with engine.begin() as conn:
        conn.execute(
            text('UPDATE alembic_version SET version_num = :stamp'),
            {'stamp': old_stamp},
        )
        conn.execute(text("INSERT INTO sandbox_spec_access VALUES ('private', true)"))
        if old_stamp == '224':
            conn.execute(
                text(
                    "INSERT INTO v1_remote_sandbox (id, sandbox_spec_id, sandbox_image) VALUES ('existing', 'private', 'image:1')"
                )
            )
        conn.execute(
            text("""INSERT INTO auth_providers (kind, title, is_idp, details)
            VALUES ('OAuthProviderDetails', 'Bitbucket', false,
            '{"provider_type":"bitbucket_data_center","scopes":["PROJECT_READ","REPO_READ","REPO_WRITE"]}')""")
        )
    migrate('upgrade', 'head')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '229'
        assert conn.scalar(
            text(
                "SELECT is_private FROM sandbox_spec_access WHERE sandbox_name = 'private'"
            )
        )
        assert conn.scalar(
            text(
                "SELECT details -> 'scopes' FROM auth_providers WHERE title = 'Bitbucket'"
            )
        ) == ['REPO_ADMIN']
        if old_stamp == '224':
            assert (
                conn.scalar(
                    text(
                        "SELECT sandbox_image FROM v1_remote_sandbox WHERE id = 'existing'"
                    )
                )
                == 'image:1'
            )
