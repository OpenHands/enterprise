"""Real-DB tests for migration 190 (convert legacy Keycloak login IDPs).

Rows seeded by the earlier migration 189 (``provider_type`` set to the brand,
optionally ``legacy_keycloak = true``) are rewritten to
``provider_type='enterprise_sso'`` + ``legacy_keycloak_type=<brand>``; rows
that are not Keycloak-federated brand logins are left alone.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

KC_AUTH_URL = 'https://auth.example.com/realms/r/protocol/openid-connect/auth'


def _insert_oauth_row(engine: Engine, title: str, is_idp: bool, details: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', :title, :is_idp, '#000000', true,
                     :details)
                RETURNING id
                """
            ),
            {
                'title': title,
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


def test_upgrade_converts_keycloak_federated_brand_rows(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '189'
    )
    flagged = _insert_oauth_row(
        engine,
        'Log in with GitHub',
        True,
        {
            'provider_type': 'github',
            'authorization_url': f'{KC_AUTH_URL}?kc_idp_hint=github',
            'legacy_keycloak': True,
        },
    )
    unflagged = _insert_oauth_row(
        engine,
        'Log in with GitLab',
        True,
        {
            'provider_type': 'gitlab',
            'authorization_url': f'{KC_AUTH_URL}?x=1&kc_idp_hint=gitlab&y=2',
        },
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '190'
    )

    for row_id, brand in ((flagged, 'github'), (unflagged, 'gitlab')):
        details = _details(engine, row_id)
        assert details['provider_type'] == 'enterprise_sso'
        assert details['legacy_keycloak_type'] == brand
        assert 'legacy_keycloak' not in details
        assert details['client_id'] == 'cid'


def test_upgrade_leaves_other_rows_alone(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '189'
    )
    direct_idp = {
        'provider_type': 'github',
        'authorization_url': 'https://github.com/login/oauth/authorize',
    }
    git_provider = {
        'provider_type': 'github',
        'authorization_url': f'{KC_AUTH_URL}?kc_idp_hint=github',
    }
    mismatched_hint = {
        'provider_type': 'github',
        'authorization_url': f'{KC_AUTH_URL}?kc_idp_hint=gitlab',
    }
    already_converted = {
        'provider_type': 'enterprise_sso',
        'authorization_url': f'{KC_AUTH_URL}?kc_idp_hint=github',
        'legacy_keycloak_type': 'github',
    }
    ids = [
        _insert_oauth_row(engine, 'GitHub', True, direct_idp),
        _insert_oauth_row(engine, 'GitHub git', False, git_provider),
        _insert_oauth_row(engine, 'Odd', True, mismatched_hint),
        _insert_oauth_row(engine, 'Converted', True, already_converted),
    ]

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '190'
    )

    expected = [direct_idp, git_provider, mismatched_hint, already_converted]
    for row_id, original in zip(ids, expected, strict=False):
        details = _details(engine, row_id)
        assert {k: details[k] for k in original} == original
        assert details.keys() == {'kind', 'client_id', *original}
