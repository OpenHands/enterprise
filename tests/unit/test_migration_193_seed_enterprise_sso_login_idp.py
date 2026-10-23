"""Real-DB tests for migration 193 (seed the ``Login with Enterprise SSO`` IDP)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

_AUTH_URL = (
    'https://auth.staging.all-hands.dev/realms/allhands'
    '/protocol/openid-connect/auth?kc_idp_hint=enterprise_sso'
)


def _auth_provider_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT id, title, is_idp, icon, color, allow_registration, details '
                'FROM auth_providers ORDER BY id'
            )
        ).mappings()
        return [dict(row) for row in rows]


@pytest.fixture
def at_192(
    monkeypatch,
    test_database: postgres_testdb.TestDatabase,
) -> postgres_testdb.TestDatabase:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '192'
    )
    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setenv('KEYCLOAK_CLIENT_ID', 'kc-client')
    monkeypatch.delenv('ENABLE_ENTERPRISE_SSO', raising=False)
    return test_database


def _upgrade(db: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(db.server, db.name, 'upgrade', '193')


def _insert_enterprise_sso_row(engine: Engine, title: str, authorization_url: str):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', :title, true, '#06c', true, :details)
                """
            ),
            {
                'title': title,
                'details': json.dumps(
                    {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'enterprise_sso',
                        'client_id': 'kc-client',
                        'authorization_url': authorization_url,
                    }
                ),
            },
        )


def test_upgrade_seeds_enterprise_sso_row(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')

    _upgrade(at_192)

    (row,) = _auth_provider_rows(engine)
    assert row['title'] == 'Login with Enterprise SSO'
    assert row['is_idp'] is True
    assert row['color'] == '#374151'
    assert row['allow_registration'] is True
    assert row['icon'].startswith('<svg')
    details = row['details']
    assert details['provider_type'] == 'enterprise_sso'
    assert details['client_id'] == 'kc-client'
    assert details['authorization_url'] == _AUTH_URL
    assert details['token_url'] == (
        'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/token'
    )
    assert details.get('legacy_keycloak_type') is None


@pytest.mark.parametrize('value', ['1', 'enabled', 'false'])
def test_upgrade_gate_accepts_any_non_empty_value(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase, value: str
):
    """Same truthiness as ``ServerConfig.get_config``: any non-empty string."""
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', value)

    _upgrade(at_192)

    assert len(_auth_provider_rows(engine)) == 1


@pytest.mark.parametrize('value', [None, '', '   '])
def test_upgrade_without_enable_flag_seeds_nothing(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase, value
):
    if value is not None:
        monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', value)

    _upgrade(at_192)

    assert _auth_provider_rows(engine) == []


def test_upgrade_without_keycloak_client_id_seeds_nothing(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    monkeypatch.delenv('KEYCLOAK_CLIENT_ID', raising=False)

    _upgrade(at_192)

    assert _auth_provider_rows(engine) == []


def test_upgrade_is_independent_of_git_provider_brands(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    """SSO sat alongside the brand buttons, so brands being configured must
    not suppress it."""
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client')

    _upgrade(at_192)

    assert [r['title'] for r in _auth_provider_rows(engine)] == [
        'Login with Enterprise SSO'
    ]


def test_upgrade_is_idempotent_when_hinted_row_exists(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    _insert_enterprise_sso_row(engine, 'Corporate SSO', _AUTH_URL)

    _upgrade(at_192)

    assert [r['title'] for r in _auth_provider_rows(engine)] == ['Corporate SSO']


def test_upgrade_ignores_row_with_different_hint(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    """A ``kc_idp_hint`` that merely starts with ``enterprise_sso`` is another
    IdP, not this one."""
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    _insert_enterprise_sso_row(
        engine, 'Other IdP', _AUTH_URL.replace('enterprise_sso', 'enterprise_sso2')
    )

    _upgrade(at_192)

    assert [r['title'] for r in _auth_provider_rows(engine)] == [
        'Other IdP',
        'Login with Enterprise SSO',
    ]


def test_downgrade_removes_only_the_seeded_row(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    _insert_enterprise_sso_row(
        engine, 'Corporate SSO', _AUTH_URL.replace('enterprise_sso', 'corp')
    )
    _upgrade(at_192)
    assert len(_auth_provider_rows(engine)) == 2

    postgres_testdb.run_alembic(at_192.server, at_192.name, 'downgrade', '192')

    assert [r['title'] for r in _auth_provider_rows(engine)] == ['Corporate SSO']


def test_downgrade_keeps_admin_row_sharing_the_hint(
    monkeypatch, engine: Engine, at_192: postgres_testdb.TestDatabase
):
    monkeypatch.setenv('ENABLE_ENTERPRISE_SSO', 'true')
    _insert_enterprise_sso_row(engine, 'Corporate SSO', _AUTH_URL)
    _upgrade(at_192)

    postgres_testdb.run_alembic(at_192.server, at_192.name, 'downgrade', '192')

    assert [r['title'] for r in _auth_provider_rows(engine)] == ['Corporate SSO']
