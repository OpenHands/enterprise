"""Real-DB tests for migration 223 (Bitbucket scopes)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

CLOUD_SEEDED = ['repository', 'account']
CLOUD_LEGACY = [
    'account',
    'repository:write',
    'project:write',
    'pullrequest:write',
    'issue:write',
    'snippet',
    'email',
    'pipeline',
]
DC_SEEDED = ['PROJECT_READ', 'REPO_READ', 'REPO_WRITE']
DC_LEGACY = ['REPO_WRITE']


def _insert_oauth_row(engine: Engine, details: dict, is_idp: bool = False) -> int:
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


@pytest.fixture
def at_222(engine: Engine, test_database: postgres_testdb.TestDatabase):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    return test_database


def _upgrade_to_223(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '223'
    )


def test_upgrade_replaces_seeded_scopes(engine: Engine, at_222):
    cloud = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket', 'scopes': CLOUD_SEEDED}
    )
    cloud_login = _insert_oauth_row(
        engine,
        {'provider_type': 'bitbucket', 'scopes': CLOUD_SEEDED},
        is_idp=True,
    )
    dc = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket_data_center', 'scopes': DC_SEEDED}
    )

    _upgrade_to_223(at_222)

    assert _details(engine, cloud)['scopes'] == CLOUD_LEGACY
    assert _details(engine, cloud_login)['scopes'] == CLOUD_LEGACY
    assert _details(engine, dc)['scopes'] == DC_LEGACY
    assert _details(engine, cloud)['client_id'] == 'cid'


def test_upgrade_leaves_other_rows_alone(engine: Engine, at_222):
    custom_cloud = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket', 'scopes': ['repository']}
    )
    custom_dc = _insert_oauth_row(
        engine,
        {
            'provider_type': 'bitbucket_data_center',
            'scopes': ['REPO_READ', 'REPO_ADMIN'],
        },
    )
    no_scopes = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket', 'scopes': None}
    )
    wrong_brand = _insert_oauth_row(
        engine, {'provider_type': 'github', 'scopes': CLOUD_SEEDED}
    )
    swapped = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket', 'scopes': DC_SEEDED}
    )

    _upgrade_to_223(at_222)

    assert _details(engine, custom_cloud)['scopes'] == ['repository']
    assert _details(engine, custom_dc)['scopes'] == ['REPO_READ', 'REPO_ADMIN']
    assert _details(engine, no_scopes)['scopes'] is None
    assert _details(engine, wrong_brand)['scopes'] == CLOUD_SEEDED
    assert _details(engine, swapped)['scopes'] == DC_SEEDED


def test_downgrade_restores_seeded_scopes(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    cloud = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket', 'scopes': CLOUD_LEGACY}
    )
    dc = _insert_oauth_row(
        engine, {'provider_type': 'bitbucket_data_center', 'scopes': DC_LEGACY}
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )

    assert _details(engine, cloud)['scopes'] == CLOUD_SEEDED
    assert _details(engine, dc)['scopes'] == DC_SEEDED
