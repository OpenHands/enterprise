"""Tests for migration 185 (at most one ``integrated_idp`` row, always
``is_idp=True``).

Mirrors the structure of ``tests/unit/storage/test_jira_dc_active_link_constraint.py``:
a mocked-``op`` check that ``upgrade()`` issues the expected DDL, plus
real-Postgres tests (the ``engine``/``test_database`` fixtures, already
migrated to head) proving the constraints actually enforce the invariant.
"""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '185_enforce_single_integrated_idp_provider.py'
)
spec = spec_from_file_location('migration_185', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_185 = module_from_spec(spec)
spec.loader.exec_module(migration_185)


# ── upgrade() issues the expected DDL ───────────────────────────────────────


def test_upgrade_creates_partial_unique_index():
    operations = Mock()
    with patch.object(migration_185, 'op', operations):
        migration_185.upgrade()

    args, kwargs = operations.create_index.call_args
    assert args == (
        migration_185.UNIQUE_INDEX_NAME,
        'oauth_providers',
        ['provider_category'],
    )
    assert kwargs['unique'] is True
    assert str(kwargs['postgresql_where']) == "provider_category = 'integrated_idp'"


def test_upgrade_creates_check_constraint():
    operations = Mock()
    with patch.object(migration_185, 'op', operations):
        migration_185.upgrade()

    args, kwargs = operations.create_check_constraint.call_args
    assert args == (
        migration_185.CHECK_CONSTRAINT_NAME,
        'oauth_providers',
        "provider_category <> 'integrated_idp' OR is_idp",
    )
    assert kwargs == {}


def test_downgrade_drops_constraint_then_index():
    operations = Mock()
    with patch.object(migration_185, 'op', operations):
        migration_185.downgrade()

    operations.drop_constraint.assert_called_once_with(
        migration_185.CHECK_CONSTRAINT_NAME, 'oauth_providers', type_='check'
    )
    operations.drop_index.assert_called_once_with(
        migration_185.UNIQUE_INDEX_NAME, table_name='oauth_providers'
    )


# ── real-DB enforcement (head already includes migration 185) ──────────────
#
# Migration 186 renamed ``oauth_providers`` to ``auth_providers`` and
# collapsed ``provider_category`` into ``kind``/``details`` -- see that
# migration's module docstring. It also re-created migration 185's "at most
# one integrated/password IDP row" guard on the new columns
# (``uq_auth_providers_one_password_provider`` /
# ``ck_auth_providers_password_provider_is_idp``, keyed off
# ``kind == 'PasswordProviderDetails'``), so these tests insert against the
# post-186 shape to prove that invariant survives at head.


def _insert_provider(
    engine: Engine,
    *,
    kind: str,
    is_idp: bool,
    details: dict,
    title: str = 'Test Provider',
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_providers (kind, title, is_idp, details)
                VALUES (:kind, :title, :is_idp, :details)
                """
            ),
            {
                'kind': kind,
                'title': title,
                'is_idp': is_idp,
                'details': json.dumps(details),
            },
        )


def test_single_integrated_idp_row_can_be_inserted(engine: Engine):
    _insert_provider(
        engine,
        kind='PasswordProviderDetails',
        is_idp=True,
        details={'kind': 'PasswordProviderDetails'},
    )
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM auth_providers WHERE kind = 'PasswordProviderDetails'"
            )
        ).scalar_one()
    assert count == 1


def test_second_integrated_idp_row_violates_unique_index(engine: Engine):
    _insert_provider(
        engine,
        kind='PasswordProviderDetails',
        is_idp=True,
        details={'kind': 'PasswordProviderDetails'},
    )
    with pytest.raises(IntegrityError, match='uq_auth_providers_one_password_provider'):
        _insert_provider(
            engine,
            kind='PasswordProviderDetails',
            is_idp=True,
            details={'kind': 'PasswordProviderDetails'},
        )


def test_integrated_idp_row_with_is_idp_false_violates_check_constraint(
    engine: Engine,
):
    # The pg8000 dialect only maps the Postgres "unique_violation" error code
    # (23505) to ``IntegrityError``; a "check_violation" (23514) surfaces as
    # the more general ``DBAPIError`` -- both are DB-level rejections, so
    # either is an acceptable proof the constraint fired.
    with pytest.raises(DBAPIError, match='ck_auth_providers_password_provider_is_idp'):
        _insert_provider(
            engine,
            kind='PasswordProviderDetails',
            is_idp=False,
            details={'kind': 'PasswordProviderDetails'},
        )


def test_non_integrated_idp_rows_are_unrestricted(engine: Engine):
    """The unique index/check constraint only apply to
    ``kind == 'PasswordProviderDetails'`` -- every other kind (git
    providers, real IDPs) can have any number of rows, any ``is_idp`` value."""
    _insert_provider(
        engine,
        kind='OAuthProviderDetails',
        is_idp=False,
        details={
            'kind': 'OAuthProviderDetails',
            'provider_type': 'github',
            'client_id': 'gh-1',
        },
    )
    _insert_provider(
        engine,
        kind='OAuthProviderDetails',
        is_idp=False,
        details={
            'kind': 'OAuthProviderDetails',
            'provider_type': 'github',
            'client_id': 'gh-2',
        },
    )
    _insert_provider(
        engine,
        kind='OAuthProviderDetails',
        is_idp=True,
        details={
            'kind': 'OAuthProviderDetails',
            'provider_type': 'enterprise_sso',
            'client_id': 'sso-1',
        },
    )
    with engine.connect() as conn:
        count = conn.execute(text('SELECT COUNT(*) FROM auth_providers')).scalar_one()
    assert count == 3
