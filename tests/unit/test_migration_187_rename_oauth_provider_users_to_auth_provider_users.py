"""Real-DB tests for migration 187 (``oauth_provider_users`` ->
``auth_provider_users``).

A plain table rename with two renamed indexes and no column/data changes, so
(unlike 186) these tests just assert the rename round-trips: same row ids
before/after, data readable under the new/old name, and the index names
match what the model (``storage.auth_provider_user.AuthProviderUser``)
expects.
"""

from __future__ import annotations

import json
import uuid
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from storage.user import User
from tests import postgres_testdb


def _insert_provider(engine: Engine) -> int:
    """Insert a minimal post-186 ``auth_providers`` row (password/IDP kind,
    which needs no further fields in ``details``)."""
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers (kind, title, is_idp, details)
                VALUES ('PasswordProviderDetails', 'Integrated IDP', true, :details)
                RETURNING id
                """
            ),
            {'details': json.dumps({'kind': 'PasswordProviderDetails'})},
        ).scalar_one()


def _insert_legacy_provider_user(
    engine: Engine,
    *,
    oauth_provider_id: int,
    user_id: uuid.UUID,
    external_subject_id: str,
    external_email: str | None = None,
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO oauth_provider_users
                    (oauth_provider_id, user_id, external_subject_id, external_email)
                VALUES
                    (:oauth_provider_id, :user_id, :external_subject_id, :external_email)
                RETURNING id
                """
            ),
            {
                'oauth_provider_id': oauth_provider_id,
                'user_id': user_id,
                'external_subject_id': external_subject_id,
                'external_email': external_email,
            },
        ).scalar_one()


def _index_names(engine: Engine, table_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT indexname FROM pg_indexes WHERE tablename = :table'),
            {'table': table_name},
        ).scalars()
        return set(rows)


def test_upgrade_renames_table_and_preserves_row_ids(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
    create_user: Callable[..., User],
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '186'
    )
    provider_id = _insert_provider(engine)
    user = create_user()
    link_id = _insert_legacy_provider_user(
        engine,
        oauth_provider_id=provider_id,
        user_id=user.id,
        external_subject_id='ext-123',
        external_email='person@example.com',
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '187'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT id, oauth_provider_id, user_id, external_subject_id, '
                    'external_email FROM auth_provider_users WHERE id = :id'
                ),
                {'id': link_id},
            )
            .mappings()
            .one()
        )

    assert row['id'] == link_id
    assert row['oauth_provider_id'] == provider_id
    assert row['user_id'] == user.id
    assert row['external_subject_id'] == 'ext-123'
    assert row['external_email'] == 'person@example.com'


def test_upgrade_renames_indexes(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '186'
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '187'
    )

    index_names = _index_names(engine, 'auth_provider_users')
    assert 'uq_auth_provider_users_provider_subject' in index_names
    assert 'ix_auth_provider_users_user_provider' in index_names
    assert 'uq_oauth_provider_users_provider_subject' not in index_names
    assert 'ix_oauth_provider_users_user_provider' not in index_names


def test_downgrade_restores_table_name_and_indexes(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
    create_user: Callable[..., User],
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '186'
    )
    provider_id = _insert_provider(engine)
    user = create_user()
    link_id = _insert_legacy_provider_user(
        engine,
        oauth_provider_id=provider_id,
        user_id=user.id,
        external_subject_id='ext-456',
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '187'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '186'
    )

    with engine.connect() as conn:
        found_id = conn.execute(
            text('SELECT id FROM oauth_provider_users WHERE id = :id'),
            {'id': link_id},
        ).scalar_one_or_none()
    assert found_id == link_id

    index_names = _index_names(engine, 'oauth_provider_users')
    assert 'uq_oauth_provider_users_provider_subject' in index_names
    assert 'ix_oauth_provider_users_user_provider' in index_names
    assert 'uq_auth_provider_users_provider_subject' not in index_names
    assert 'ix_auth_provider_users_user_provider' not in index_names
