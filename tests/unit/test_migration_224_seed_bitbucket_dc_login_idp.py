"""Real-DB tests for migration 224 (seed the Bitbucket Data Center login IDP)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from openhands.app_server.integrations.service_types import ProviderType
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from tests import postgres_testdb

_ENV = {
    'AUTH_URL': 'https://auth.example.com',
    'KEYCLOAK_REALM_NAME': 'allhands',
    'KEYCLOAK_CLIENT_ID': 'kc-client',
}


def _insert_provider(engine: Engine, *, title: str, is_idp: bool, details: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', :title, :is_idp, true, :details)
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


def _insert_git_row(engine: Engine) -> int:
    return _insert_provider(
        engine,
        title='Bitbucket Data Center',
        is_idp=False,
        details={'provider_type': 'bitbucket_data_center'},
    )


@pytest.fixture
def at_223(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
) -> postgres_testdb.TestDatabase:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '223'
    )
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('KEYCLOAK_CLIENT_SECRET', raising=False)
    return test_database


def _upgrade(database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(database.server, database.name, 'upgrade', '224')


def _login_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text('SELECT * FROM auth_providers WHERE is_idp ORDER BY id')
            ).mappings()
        ]


def test_upgrade_seeds_login_row_behind_keycloak(monkeypatch, engine: Engine, at_223):
    monkeypatch.setenv('KEYCLOAK_CLIENT_SECRET', 'kc-secret')
    _insert_git_row(engine)

    _upgrade(at_223)

    (row,) = _login_rows(engine)
    assert row['title'] == 'Log in with Bitbucket Data Center'
    assert row['enabled'] is True
    assert row['allow_registration'] is True
    assert row['allow_match_by_email'] is True
    assert row['color'] == '#2684FF'
    assert row['icon'].startswith('<svg')
    details = row['details']
    assert details['provider_type'] == 'enterprise_sso'
    assert details['legacy_keycloak_type'] == 'bitbucket_data_center'
    assert details['client_id'] == 'kc-client'
    assert details['client_secret'] != 'kc-secret'
    assert details['authorization_url'] == (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/auth'
        '?kc_idp_hint=bitbucket_data_center'
    )
    assert details['token_url'].endswith('/protocol/openid-connect/token')
    assert details['userinfo_url'].endswith('/protocol/openid-connect/userinfo')


def test_seeded_row_loads_as_model_with_decrypted_secret(
    monkeypatch, engine: Engine, at_223, session_maker
):
    monkeypatch.setenv('KEYCLOAK_CLIENT_SECRET', 'kc-secret')
    _insert_git_row(engine)

    _upgrade(at_223)

    with session_maker() as session:
        provider = session.query(AuthProvider).filter(AuthProvider.is_idp).one()
        assert isinstance(provider.details, OAuthProviderDetails)
        assert provider.details.client_secret.get_secret_value() == 'kc-secret'
        assert (
            provider.details.legacy_keycloak_type == ProviderType.BITBUCKET_DATA_CENTER
        )


def test_upgrade_seeds_nothing_without_a_git_provider_row(engine: Engine, at_223):
    _insert_provider(
        engine,
        title='Bitbucket',
        is_idp=False,
        details={'provider_type': 'bitbucket'},
    )

    _upgrade(at_223)

    assert _login_rows(engine) == []


def test_upgrade_seeds_nothing_without_keycloak_client_id(
    monkeypatch, engine: Engine, at_223
):
    monkeypatch.delenv('KEYCLOAK_CLIENT_ID')
    _insert_git_row(engine)

    _upgrade(at_223)

    assert _login_rows(engine) == []


@pytest.mark.parametrize(
    'details',
    [
        {'provider_type': 'bitbucket_data_center'},
        {
            'provider_type': 'enterprise_sso',
            'legacy_keycloak_type': 'bitbucket_data_center',
        },
    ],
)
def test_upgrade_keeps_an_existing_login_row(engine: Engine, at_223, details):
    _insert_git_row(engine)
    existing = _insert_provider(
        engine, title='Our Bitbucket', is_idp=True, details=details
    )

    _upgrade(at_223)

    assert [r['id'] for r in _login_rows(engine)] == [existing]


def test_downgrade_removes_only_the_seeded_row(engine: Engine, at_223):
    _insert_git_row(engine)
    other_login = _insert_provider(
        engine,
        title='Log in with Bitbucket',
        is_idp=True,
        details={
            'provider_type': 'enterprise_sso',
            'legacy_keycloak_type': 'bitbucket',
        },
    )
    _upgrade(at_223)
    assert len(_login_rows(engine)) == 2

    postgres_testdb.run_alembic(at_223.server, at_223.name, 'downgrade', '223')

    assert [r['id'] for r in _login_rows(engine)] == [other_login]
    with engine.connect() as conn:
        assert (
            conn.execute(
                text('SELECT COUNT(*) FROM auth_providers WHERE NOT is_idp')
            ).scalar_one()
            == 1
        )
