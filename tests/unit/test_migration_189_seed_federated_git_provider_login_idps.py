"""Real-DB tests for migration 189 (seed GitHub/GitLab/Bitbucket login IDPs
routed through Keycloak).

The migration only has anything to do once an ``auth_providers`` Keycloak
IDP row exists (seeded by migration 168, gated on ``KEYCLOAK_CLIENT_ID`` --
not set while building the test template), so every test downgrades to 188
and inserts that row by hand first.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

_KEYCLOAK_DETAILS = {
    'kind': 'OAuthProviderDetails',
    'provider_type': 'enterprise_sso',
    'client_id': 'allhands',
    'client_secret': 'ciphertext-does-not-need-decrypting-in-this-test',
    'authorization_url': (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/auth'
    ),
    'token_url': (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/token'
    ),
    'userinfo_url': (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/userinfo'
    ),
    'scopes': ['openid', 'email', 'profile'],
    'permitted_drift_seconds': 60,
}


def _downgrade_to_188(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )


def _upgrade_to_189(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )


def _insert_keycloak_idp_row(engine: Engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', 'Keycloak', true, '#000000', true,
                     :details)
                RETURNING id
                """
            ),
            {'details': json.dumps(_KEYCLOAK_DETAILS)},
        ).scalar_one()


def _federated_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT id, title, color, icon, is_idp, kind, details
                    FROM auth_providers
                    WHERE details ->> 'provider_type' <> 'enterprise_sso'
                    ORDER BY id
                    """
                )
            )
            .mappings()
            .all()
        )
        out = []
        for row in rows:
            d = dict(row)
            details = d['details']
            d['details'] = json.loads(details) if isinstance(details, str) else details
            out.append(d)
        return out


@pytest.fixture(autouse=True)
def _clear_git_provider_env(monkeypatch):
    for key in (
        'GITHUB_APP_CLIENT_ID',
        'GITLAB_APP_CLIENT_ID',
        'BITBUCKET_APP_CLIENT_ID',
    ):
        monkeypatch.delenv(key, raising=False)


def test_upgrade_adds_a_row_per_configured_brand(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)

    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')
    monkeypatch.setenv('GITLAB_APP_CLIENT_ID', 'gl-client-id')
    _upgrade_to_189(test_database)

    rows = _federated_rows(engine)
    provider_types = {row['details']['provider_type'] for row in rows}
    assert provider_types == {'github', 'gitlab'}

    for row in rows:
        assert row['is_idp'] is True
        assert row['kind'] == 'OAuthProviderDetails'
        assert row['icon']
        # Reused verbatim from the Keycloak row.
        assert row['details']['client_id'] == _KEYCLOAK_DETAILS['client_id']
        assert row['details']['client_secret'] == _KEYCLOAK_DETAILS['client_secret']
        assert row['details']['token_url'] == _KEYCLOAK_DETAILS['token_url']
        assert row['details']['userinfo_url'] == _KEYCLOAK_DETAILS['userinfo_url']
        assert row['details']['scopes'] == _KEYCLOAK_DETAILS['scopes']
        # Overridden: provider_type + kc_idp_hint appended to the auth URL.
        expected_hint = f'kc_idp_hint={row["details"]["provider_type"]}'
        assert row['details']['authorization_url'] == (
            f'{_KEYCLOAK_DETAILS["authorization_url"]}?{expected_hint}'
        )

    github_row = next(r for r in rows if r['details']['provider_type'] == 'github')
    assert github_row['title'] == 'Log in with GitHub'
    assert github_row['color'] == '#9E28B0'

    gitlab_row = next(r for r in rows if r['details']['provider_type'] == 'gitlab')
    assert gitlab_row['title'] == 'Log in with GitLab'
    assert gitlab_row['color'] == '#FC6B0E'


def test_upgrade_adds_bitbucket_row(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)

    monkeypatch.setenv('BITBUCKET_APP_CLIENT_ID', 'bb-client-id')
    _upgrade_to_189(test_database)

    rows = _federated_rows(engine)
    assert len(rows) == 1
    assert rows[0]['details']['provider_type'] == 'bitbucket'
    assert rows[0]['title'] == 'Log in with Bitbucket'
    assert rows[0]['color'] == '#2684FF'
    assert rows[0]['details']['authorization_url'] == (
        f'{_KEYCLOAK_DETAILS["authorization_url"]}?kc_idp_hint=bitbucket'
    )


def test_upgrade_appends_hint_with_ampersand_when_url_already_has_a_query(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    details = dict(_KEYCLOAK_DETAILS)
    details['authorization_url'] = (
        f'{_KEYCLOAK_DETAILS["authorization_url"]}?prompt=login'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', 'Keycloak', true, '#000000', true,
                     :details)
                """
            ),
            {'details': json.dumps(details)},
        )

    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')
    _upgrade_to_189(test_database)

    rows = _federated_rows(engine)
    assert rows[0]['details']['authorization_url'] == (
        f'{_KEYCLOAK_DETAILS["authorization_url"]}?prompt=login&kc_idp_hint=github'
    )


def test_upgrade_noop_without_any_git_provider_env(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)

    _upgrade_to_189(test_database)

    assert _federated_rows(engine) == []


def test_upgrade_noop_without_a_keycloak_idp_row(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    # No Keycloak row inserted this time.

    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')
    _upgrade_to_189(test_database)

    assert _federated_rows(engine) == []


def test_upgrade_is_idempotent(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')
    _upgrade_to_189(test_database)
    assert len(_federated_rows(engine)) == 1

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )

    assert len(_federated_rows(engine)) == 1


def test_downgrade_removes_seeded_rows_but_not_the_keycloak_row(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')
    monkeypatch.setenv('GITLAB_APP_CLIENT_ID', 'gl-client-id')
    monkeypatch.setenv('BITBUCKET_APP_CLIENT_ID', 'bb-client-id')
    _upgrade_to_189(test_database)
    assert len(_federated_rows(engine)) == 3

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    assert _federated_rows(engine) == []
    with engine.connect() as conn:
        remaining_titles = (
            conn.execute(text('SELECT title FROM auth_providers')).scalars().all()
        )
    assert remaining_titles == ['Keycloak']
