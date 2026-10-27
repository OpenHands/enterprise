"""Tests for migration 192 (enable ``allow_match_by_email`` for unlinked users).

Runs the migration's ``upgrade()`` against a real Postgres database seeded with
users in each state the migration distinguishes. Migration 197 drops the column
again, so each test first re-adds it through that migration's ``downgrade()``
while the rest of the schema stays at head: later migrations add columns the
ORM fixtures insert through (e.g. 201 ``org.meta_profiles``), which a full
downgrade to 196 would remove.
"""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import Engine

VERSIONS_DIR = Path(__file__).resolve().parents[2] / 'migrations' / 'versions'


def _load_migration(filename: str):
    spec = spec_from_file_location(filename.split('_', 1)[0], VERSIONS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration_192 = _load_migration('192_enable_allow_match_by_email_for_unlinked_users.py')
migration_197 = _load_migration('197_drop_user_allow_match_by_email.py')


@pytest.fixture(autouse=True)
def _before_column_dropped(engine: Engine):
    with engine.begin() as conn:
        migration_197.op = Operations(MigrationContext.configure(conn))
        migration_197.downgrade()


def _flag(engine: Engine, user_id) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            text('SELECT allow_match_by_email FROM "user" WHERE id = :id'),
            {'id': user_id},
        ).scalar_one()


def _link_user(engine: Engine, user_id) -> None:
    with engine.begin() as conn:
        provider_id = conn.execute(
            text(
                """
                INSERT INTO auth_providers (kind, title, is_idp, details)
                VALUES ('OAuthProviderDetails', 'Test IDP', true, :details)
                RETURNING id
                """
            ),
            {'details': json.dumps({'kind': 'OAuthProviderDetails'})},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO auth_provider_users
                    (oauth_provider_id, user_id, external_subject_id)
                VALUES (:provider_id, :user_id, 'sub-1')
                """
            ),
            {'provider_id': provider_id, 'user_id': user_id},
        )


def _run_upgrade(engine: Engine) -> None:
    with engine.begin() as conn:
        migration_192.op = SimpleNamespace(get_bind=lambda: conn)
        migration_192.upgrade()


def test_upgrade_enables_flag_for_unlinked_users(engine: Engine, create_user):
    user = create_user(email='unlinked@example.com', email_verified=True)

    _run_upgrade(engine)

    assert _flag(engine, user.id) is True


def test_upgrade_leaves_linked_users_untouched(engine: Engine, create_user):
    linked = create_user(email='linked@example.com', email_verified=True)
    _link_user(engine, linked.id)

    _run_upgrade(engine)

    assert _flag(engine, linked.id) is False


@pytest.mark.parametrize('email_verified', [False, None])
def test_upgrade_skips_users_without_verified_email(
    engine: Engine, create_user, email_verified
):
    user = create_user(email='unverified@example.com', email_verified=email_verified)

    _run_upgrade(engine)

    assert _flag(engine, user.id) is False


def test_upgrade_is_idempotent(engine: Engine, create_user):
    user = create_user(email='unlinked@example.com', email_verified=True)

    _run_upgrade(engine)
    _run_upgrade(engine)

    assert _flag(engine, user.id) is True
