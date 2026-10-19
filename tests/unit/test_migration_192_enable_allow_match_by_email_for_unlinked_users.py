"""Tests for migration 192 (enable ``allow_match_by_email`` for unlinked users).

Runs the migration's ``upgrade()`` against a real Postgres database (already
migrated to head) seeded with users in each state the migration distinguishes.
"""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '192_enable_allow_match_by_email_for_unlinked_users.py'
)
spec = spec_from_file_location('migration_192', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_192 = module_from_spec(spec)
spec.loader.exec_module(migration_192)


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
