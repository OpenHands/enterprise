"""Real-DB tests for migration 189 (seed federated git-provider login IDPs).

Covers ``legacy_keycloak_type`` on ``OAuthProviderDetails``: every per-brand
row this migration seeds is an ``enterprise_sso`` row carrying
``details.legacy_keycloak_type = <brand>``, since its ``token_url``/
credentials are actually Keycloak's own, not the brand's -- see
``storage.auth_provider_details.OAuthProviderDetails.legacy_keycloak_type``
and ``server.routes.oauth_v2``'s callback, which uses it to fetch the real
git-provider token from Keycloak's identity-brokering endpoint instead of
trusting this row's own token exchange.

The rest of migration 189's behavior (retiring the generic Keycloak row,
rebranding the password login) is unrelated to this field and already
exercised by the migration's own docstring/behavior; not re-tested here.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _auth_provider_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT id, title, is_idp, details FROM auth_providers ORDER BY id')
        ).mappings()
        return [dict(row) for row in rows]


def test_upgrade_seeds_legacy_keycloak_type_on_federated_login_rows(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setenv('KEYCLOAK_CLIENT_ID', 'kc-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client')
    monkeypatch.delenv('GITLAB_APP_CLIENT_ID', raising=False)
    monkeypatch.delenv('BITBUCKET_APP_CLIENT_ID', raising=False)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    rows = _auth_provider_rows(engine)
    github_rows = [
        r for r in rows if r['details'].get('legacy_keycloak_type') == 'github'
    ]
    assert len(github_rows) == 1
    github_row = github_rows[0]
    assert github_row['is_idp'] is True
    assert github_row['details']['provider_type'] == 'enterprise_sso'
    assert github_row['details']['legacy_keycloak_type'] == 'github'

    # Only the gated (GITHUB_APP_CLIENT_ID) brand is seeded -- GitLab/
    # Bitbucket must not appear since their env vars are unset.
    assert {r['details'].get('legacy_keycloak_type') for r in rows} == {'github'}


def test_upgrade_without_keycloak_client_id_seeds_nothing(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    """No ``KEYCLOAK_CLIENT_ID`` -- no Keycloak client to route any of these
    rows through, so nothing is seeded even if a brand's app client id is
    set."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    monkeypatch.delenv('KEYCLOAK_CLIENT_ID', raising=False)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    assert _auth_provider_rows(engine) == []


def test_downgrade_removes_legacy_keycloak_rows(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setenv('KEYCLOAK_CLIENT_ID', 'kc-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )
    assert len(_auth_provider_rows(engine)) == 1

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    # The per-brand row is gone; the generic Keycloak row is restored.
    assert [r['title'] for r in _auth_provider_rows(engine)] == ['Keycloak']


def _insert_legacy_keycloak_row(engine: Engine) -> int:
    """Pre-seed a 189-shaped row directly, as if an earlier run of this
    migration had already created it -- proves the (idempotent) re-run
    skips an existing row rather than erroring or duplicating it."""
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', 'Log in with GitHub', true,
                     '#9E28B0', true, :details)
                RETURNING id
                """
            ),
            {
                'details': json.dumps(
                    {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'enterprise_sso',
                        'client_id': 'kc-client',
                        'legacy_keycloak_type': 'github',
                    }
                )
            },
        ).scalar_one()


def test_upgrade_is_idempotent_when_row_already_exists(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    existing_id = _insert_legacy_keycloak_row(engine)

    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setenv('KEYCLOAK_CLIENT_ID', 'kc-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    rows = _auth_provider_rows(engine)
    github_rows = [
        r for r in rows if r['details'].get('legacy_keycloak_type') == 'github'
    ]
    assert len(github_rows) == 1
    assert github_rows[0]['id'] == existing_id


def test_upgrade_skips_brand_with_existing_direct_idp(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    """An admin-configured direct GitHub login IDP (``provider_type='github'``
    on an ``is_idp`` row) already covers the brand, so none is seeded."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', 'GitHub', true, '#000000', true,
                     :details)
                """
            ),
            {
                'details': json.dumps(
                    {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'github',
                        'client_id': 'direct-client',
                    }
                )
            },
        )

    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setenv('KEYCLOAK_CLIENT_ID', 'kc-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    rows = _auth_provider_rows(engine)
    assert [r['details']['client_id'] for r in rows] == ['direct-client']
