"""Real-DB tests for migration 188 (``icon``/``color``/``allow_registration``
on ``auth_providers``).

Adds three nullable-or-defaulted columns to an existing table, so the only
things worth asserting are: existing rows backfill to the documented
defaults, a freshly-inserted row can set them explicitly, and downgrade drops
all three columns cleanly.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _insert_provider_pre_188(engine: Engine) -> int:
    """Insert a post-187, pre-188 ``auth_providers`` row (no icon/color/
    allow_registration columns yet)."""
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


def _columns(engine: Engine, table_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT column_name FROM information_schema.columns '
                'WHERE table_name = :table'
            ),
            {'table': table_name},
        ).scalars()
        return set(rows)


def _insert_oauth_provider_pre_188(engine: Engine) -> int:
    """Insert a post-187, pre-188 ``auth_providers`` row of a non-password
    kind, to prove the password-specific color override below is scoped to
    ``PasswordProviderDetails`` and does not leak onto every row."""
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers (kind, title, is_idp, details)
                VALUES ('OAuthProviderDetails', 'Keycloak', true, :details)
                RETURNING id
                """
            ),
            {
                'details': json.dumps(
                    {'kind': 'OAuthProviderDetails', 'provider_type': 'enterprise_sso'}
                )
            },
        ).scalar_one()


def test_upgrade_backfills_defaults_for_existing_rows(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '187'
    )
    provider_id = _insert_oauth_provider_pre_188(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '188'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT icon, color, allow_registration FROM auth_providers '
                    'WHERE id = :id'
                ),
                {'id': provider_id},
            )
            .mappings()
            .one()
        )

    assert row['icon'] is None
    assert row['color'] == '#000000'
    assert row['allow_registration'] is True


def test_upgrade_gives_the_password_idp_row_its_own_color(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '187'
    )
    provider_id = _insert_provider_pre_188(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '188'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text('SELECT color FROM auth_providers WHERE id = :id'),
                {'id': provider_id},
            )
            .mappings()
            .one()
        )

    assert row['color'] == '#03A956'


def test_upgrade_allows_explicit_values_on_new_rows(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '188'
    )

    with engine.begin() as conn:
        provider_id = conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, icon, color, allow_registration, details)
                VALUES
                    ('PasswordProviderDetails', 'Corp SSO', true,
                     '<svg></svg>', '#ff0000', false, :details)
                RETURNING id
                """
            ),
            {'details': json.dumps({'kind': 'PasswordProviderDetails'})},
        ).scalar_one()

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT icon, color, allow_registration FROM auth_providers '
                    'WHERE id = :id'
                ),
                {'id': provider_id},
            )
            .mappings()
            .one()
        )

    assert row['icon'] == '<svg></svg>'
    assert row['color'] == '#ff0000'
    assert row['allow_registration'] is False


def test_downgrade_drops_new_columns(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '188'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '187'
    )

    columns = _columns(engine, 'auth_providers')
    assert 'icon' not in columns
    assert 'color' not in columns
    assert 'allow_registration' not in columns
