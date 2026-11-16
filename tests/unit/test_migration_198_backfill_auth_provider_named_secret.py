"""Real-DB tests for migration 198 (backfill ``details.named_secret``)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _insert_oauth_row(engine: Engine, is_idp: bool, details: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers (kind, title, is_idp, details)
                VALUES ('OAuthProviderDetails', 'Provider', :is_idp, :details)
                RETURNING id
                """
            ),
            {
                'is_idp': is_idp,
                'details': json.dumps(
                    {'kind': 'OAuthProviderDetails', 'client_id': 'cid', **details}
                ),
            },
        ).scalar_one()


def _details(engine: Engine, row_id: int) -> dict:
    with engine.connect() as conn:
        return conn.execute(
            text('SELECT details FROM auth_providers WHERE id = :id'),
            {'id': row_id},
        ).scalar_one()


def test_upgrade_names_git_provider_rows_after_their_provider_type(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '197'
    )
    github = _insert_oauth_row(engine, False, {'provider_type': 'github'})
    bitbucket_dc = _insert_oauth_row(
        engine, False, {'provider_type': 'bitbucket_data_center'}
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '198'
    )

    assert _details(engine, github)['named_secret'] == 'GITHUB_TOKEN'
    assert _details(engine, bitbucket_dc)['named_secret'] == (
        'BITBUCKET_DATA_CENTER_TOKEN'
    )
    assert _details(engine, github)['client_id'] == 'cid'


def test_upgrade_leaves_login_rows_and_existing_names_alone(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '197'
    )
    direct_login = _insert_oauth_row(engine, True, {'provider_type': 'github'})
    keycloak_login = _insert_oauth_row(
        engine,
        True,
        {'provider_type': 'enterprise_sso', 'legacy_keycloak_type': 'gitlab'},
    )
    already_named = _insert_oauth_row(
        engine, False, {'provider_type': 'gitlab', 'named_secret': 'MY_GITLAB'}
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '198'
    )

    assert 'named_secret' not in _details(engine, direct_login)
    assert 'named_secret' not in _details(engine, keycloak_login)
    assert _details(engine, already_named)['named_secret'] == 'MY_GITLAB'


def test_downgrade_removes_named_secret(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    github = _insert_oauth_row(
        engine, False, {'provider_type': 'github', 'named_secret': 'GITHUB_TOKEN'}
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '197'
    )

    details = _details(engine, github)
    assert 'named_secret' not in details
    assert details['provider_type'] == 'github'
