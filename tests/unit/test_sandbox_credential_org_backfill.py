"""Migration 205 binds existing sandboxes from their owner's conversation records."""

import uuid
from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.sandbox.sandbox_store import REMOTE_BACKEND, StoredSandbox
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas


def test_backfill_binds_only_sandboxes_with_one_recorded_owner_org(
    session_maker, create_org, create_user
):
    org_a, org_b = create_org(), create_org()
    owner = create_user(current_org_id=org_b.id)
    other = create_user(current_org_id=org_b.id)

    def sandbox(session, conversations):
        sandbox_id = uuid.uuid4().hex
        session.add(
            StoredSandbox(
                id=sandbox_id,
                backend=REMOTE_BACKEND,
                created_by_user_id=str(owner.id),
                sandbox_spec_id='test',
            )
        )
        for user, org in conversations:
            conversation_id = uuid.uuid4().hex
            session.add(
                StoredConversationMetadata(
                    conversation_id=conversation_id, sandbox_id=sandbox_id
                )
            )
            session.add(
                StoredConversationMetadataSaas(
                    conversation_id=conversation_id, user_id=user.id, org_id=org.id
                )
            )
        return sandbox_id

    with session_maker() as session:
        one_org = sandbox(session, [(owner, org_a), (owner, org_a)])
        two_orgs = sandbox(session, [(owner, org_a), (owner, org_b)])
        other_user_ignored = sandbox(session, [(owner, org_a), (other, org_b)])
        only_other_user = sandbox(session, [(other, org_b)])
        no_conversation = sandbox(session, [])
        session.commit()

    migration = import_module('migrations.versions.205_bind_sandbox_credential_org')
    engine = session_maker.kw['bind']
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
        bound = dict(
            connection.execute(
                text('SELECT id, credential_org_id FROM v1_remote_sandbox')
            ).all()
        )

    # The owner's current org (B) is never used.
    assert bound[one_org] == org_a.id
    assert bound[other_user_ignored] == org_a.id
    assert bound[two_orgs] is None
    assert bound[only_other_user] is None
    assert bound[no_conversation] is None
