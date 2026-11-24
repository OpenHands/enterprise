"""Real-DB tests for migration 218 (seed the Enterprise SSO login IDP)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from tests import postgres_testdb

_ENV = {
    'AUTH_URL': 'https://auth.example.com',
    'KEYCLOAK_REALM_NAME': 'allhands',
    'KEYCLOAK_CLIENT_ID': 'kc-client',
}


@pytest.fixture
def at_217(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
) -> postgres_testdb.TestDatabase:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '217'
    )
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('ENABLE_ENTERPRISE_SSO', raising=False)
    monkeypatch.delenv('ENTERPRISE_SSO_DISPLAY_NAME', raising=False)
    monkeypatch.delenv('KEYCLOAK_CLIENT_SECRET', raising=False)
    return test_database


def _upgrade(database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(database.server, database.name, 'upgrade', '218')


def _rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text('SELECT * FROM auth_providers ORDER BY id')
            ).mappings()
        ]


@pytest.mark.parametrize('enable_value', ['true', '1', 'TRUE'])
def test_upgrade_seeds_enabled_login_row(
    monkeypatch, engine: Engine, at_217, enable_value
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', enable_value)
    monkeypatch.setenv('ENTERPRISE_SSO_DISPLAY_NAME', 'Acme SSO')
    monkeypatch.setenv('KEYCLOAK_CLIENT_SECRET', 'kc-secret')

    _upgrade(at_217)

    (row,) = _rows(engine)
    assert row['title'] == 'Acme SSO'
    assert row['is_idp'] is True
    assert row['enabled'] is True
    assert row['allow_registration'] is True
    assert row['allow_match_by_email'] is True
    assert row['icon'].startswith('<svg')
    details = row['details']
    assert details['provider_type'] == 'enterprise_sso'
    assert details['client_id'] == 'kc-client'
    assert details.get('legacy_keycloak_type') is None
    assert details['authorization_url'] == (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/auth'
        '?kc_idp_hint=enterprise_sso'
    )
    assert details['token_url'].endswith('/protocol/openid-connect/token')
    assert details['client_secret'] != 'kc-secret'


def test_seeded_row_loads_as_model_with_decrypted_secret(
    monkeypatch, engine: Engine, at_217, session_maker
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    monkeypatch.setenv('KEYCLOAK_CLIENT_SECRET', 'kc-secret')

    _upgrade(at_217)

    with session_maker() as session:
        provider = session.query(AuthProvider).one()
        assert isinstance(provider.details, OAuthProviderDetails)
        assert provider.details.client_secret.get_secret_value() == 'kc-secret'


def test_upgrade_defaults_title_without_display_name(
    monkeypatch, engine: Engine, at_217
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')

    _upgrade(at_217)

    assert [r['title'] for r in _rows(engine)] == ['Company SSO']


@pytest.mark.parametrize('enable_value', [None, '', 'false', '0'])
def test_upgrade_seeds_nothing_when_enterprise_sso_disabled(
    monkeypatch, engine: Engine, at_217, enable_value
):
    if enable_value is not None:
        monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', enable_value)

    _upgrade(at_217)

    assert _rows(engine) == []


def test_upgrade_seeds_nothing_without_keycloak_client_id(
    monkeypatch, engine: Engine, at_217
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    monkeypatch.delenv('KEYCLOAK_CLIENT_ID')

    _upgrade(at_217)

    assert _rows(engine) == []


def _insert_provider(engine: Engine, title: str, details: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', :title, true, '#000000', true,
                     :details)
                RETURNING id
                """
            ),
            {'title': title, 'details': json.dumps(details)},
        ).scalar_one()


def test_upgrade_is_idempotent_when_hinted_row_exists(
    monkeypatch, engine: Engine, at_217
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    existing_id = _insert_provider(
        engine,
        'Renamed by admin',
        {
            'kind': 'OAuthProviderDetails',
            'provider_type': 'enterprise_sso',
            'client_id': 'kc-client',
            'authorization_url': 'https://auth.example.com/auth?kc_idp_hint=enterprise_sso',
        },
    )

    _upgrade(at_217)

    assert [(r['id'], r['title']) for r in _rows(engine)] == [
        (existing_id, 'Renamed by admin')
    ]


def test_upgrade_seeds_alongside_direct_enterprise_sso_oidc_row(
    monkeypatch, engine: Engine, at_217
):
    """A direct OIDC ``enterprise_sso`` IDP (Okta, ...) has no Keycloak hint,
    so it must not suppress the Keycloak SAML row."""
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    direct_id = _insert_provider(
        engine,
        'Okta',
        {
            'kind': 'OAuthProviderDetails',
            'provider_type': 'enterprise_sso',
            'client_id': 'okta-client',
            'authorization_url': 'https://acme.okta.com/oauth2/v1/authorize',
        },
    )

    _upgrade(at_217)

    rows = _rows(engine)
    assert len(rows) == 2
    assert rows[0]['id'] == direct_id


def test_downgrade_removes_only_the_seeded_row(monkeypatch, engine: Engine, at_217):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    direct_id = _insert_provider(
        engine,
        'Okta',
        {
            'kind': 'OAuthProviderDetails',
            'provider_type': 'enterprise_sso',
            'client_id': 'okta-client',
            'authorization_url': 'https://acme.okta.com/oauth2/v1/authorize',
        },
    )
    _upgrade(at_217)
    assert len(_rows(engine)) == 2

    postgres_testdb.run_alembic(at_217.server, at_217.name, 'downgrade', '217')

    assert [r['id'] for r in _rows(engine)] == [direct_id]
