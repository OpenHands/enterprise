"""Real-DB tests for migration 206 (``offline_access`` on Azure DevOps rows)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

AZURE_DEVOPS_SCOPE = '499b84ac-1321-427f-aa17-267ca6975798/.default'


def _insert_oauth_row(engine: Engine, details: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers (kind, title, is_idp, details)
                VALUES ('OAuthProviderDetails', 'Provider', false, :details)
                RETURNING id
                """
            ),
            {
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


def _rerun_206(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '206'
    )


def test_upgrade_appends_offline_access_to_azure_devops_scopes(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '205'
    )
    azure = _insert_oauth_row(
        engine, {'provider_type': 'azure_devops', 'scopes': [AZURE_DEVOPS_SCOPE]}
    )

    _rerun_206(test_database)

    details = _details(engine, azure)
    assert details['scopes'] == [AZURE_DEVOPS_SCOPE, 'offline_access']
    assert details['client_id'] == 'cid'


def test_upgrade_leaves_other_rows_alone(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '205'
    )
    already_set = _insert_oauth_row(
        engine,
        {
            'provider_type': 'azure_devops',
            'scopes': ['offline_access', AZURE_DEVOPS_SCOPE],
        },
    )
    no_scopes = _insert_oauth_row(
        engine, {'provider_type': 'azure_devops', 'scopes': None}
    )
    github = _insert_oauth_row(
        engine, {'provider_type': 'github', 'scopes': ['repo', 'user:email']}
    )
    okta = _insert_oauth_row(
        engine, {'provider_type': 'enterprise_sso', 'scopes': ['openid', 'email']}
    )

    _rerun_206(test_database)

    assert _details(engine, already_set)['scopes'] == [
        'offline_access',
        AZURE_DEVOPS_SCOPE,
    ]
    assert _details(engine, no_scopes)['scopes'] is None
    assert _details(engine, github)['scopes'] == ['repo', 'user:email']
    assert _details(engine, okta)['scopes'] == ['openid', 'email']
