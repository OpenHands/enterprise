from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '178_repair_keycloak_oauth_provider_urls.py'
)
spec = spec_from_file_location('migration_178', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_178 = module_from_spec(spec)
spec.loader.exec_module(migration_178)


class _Bind:
    """Fake alembic bind that only needs to capture the UPDATE statement."""

    dialect = SimpleNamespace(name='postgresql')

    def __init__(self):
        self.writes = []

    def execute(self, statement, params=None):
        sql = ' '.join(str(statement).split())
        compiled_params = {}
        try:
            compiled_params = dict(statement.compile().params)
        except Exception:
            pass
        self.writes.append((sql, compiled_params))
        return None


def _clear_env(monkeypatch):
    for key in ('AUTH_URL', 'AUTH_WEB_HOST', 'WEB_HOST', 'KEYCLOAK_REALM_NAME'):
        monkeypatch.delenv(key, raising=False)


def _run(monkeypatch, **env):
    _clear_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    bind = _Bind()
    monkeypatch.setattr(migration_178, 'op', SimpleNamespace(get_bind=lambda: bind))
    migration_178.upgrade()
    return bind


# ── _resolve_keycloak_realm_base_url ────────────────────────────────────────


def test_resolve_prefers_auth_url(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev/')
    monkeypatch.setenv('AUTH_WEB_HOST', 'ignored.example.com')
    monkeypatch.setenv('WEB_HOST', 'ignored.example.com')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    assert (
        migration_178._resolve_keycloak_realm_base_url()
        == 'https://auth.staging.all-hands.dev/realms/allhands'
    )


def test_resolve_falls_back_to_auth_web_host(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('AUTH_WEB_HOST', 'auth.staging.all-hands.dev')
    monkeypatch.setenv('WEB_HOST', 'ignored.example.com')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    assert (
        migration_178._resolve_keycloak_realm_base_url()
        == 'https://auth.staging.all-hands.dev/realms/allhands'
    )


def test_resolve_falls_back_to_auth_subdomain_of_web_host(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('WEB_HOST', 'staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    assert (
        migration_178._resolve_keycloak_realm_base_url()
        == 'https://auth.staging.all-hands.dev/realms/allhands'
    )


def test_resolve_empty_without_any_host(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    assert migration_178._resolve_keycloak_realm_base_url() == ''


def test_resolve_empty_without_realm(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    assert migration_178._resolve_keycloak_realm_base_url() == ''


# ── upgrade ──────────────────────────────────────────────────────────────────


def test_upgrade_noop_when_no_base_url_resolves(monkeypatch):
    bind = _run(monkeypatch)
    assert bind.writes == []


def test_upgrade_updates_rows_with_broken_authorization_url(monkeypatch):
    bind = _run(
        monkeypatch,
        AUTH_URL='https://auth.staging.all-hands.dev',
        KEYCLOAK_REALM_NAME='allhands',
    )
    assert len(bind.writes) == 1
    sql, params = bind.writes[0]
    assert 'UPDATE oauth_providers' in sql
    assert 'oauth_providers.authorization_url LIKE' in sql
    assert params['authorization_url_1'] == 'http://keycloak.keycloak'
    assert (
        params['authorization_url']
        == 'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/auth'
    )
    assert (
        params['token_url'] == 'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/token'
    )
    assert (
        params['userinfo_url'] == 'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/userinfo'
    )


@pytest.mark.parametrize(
    'env',
    [
        {'AUTH_WEB_HOST': 'auth.example.com', 'KEYCLOAK_REALM_NAME': 'allhands'},
        {'WEB_HOST': 'example.com', 'KEYCLOAK_REALM_NAME': 'allhands'},
    ],
)
def test_upgrade_runs_with_fallback_derivations(monkeypatch, env):
    bind = _run(monkeypatch, **env)
    assert len(bind.writes) == 1


def test_downgrade_is_noop():
    # No fixture needed: downgrade must not touch ``op`` at all.
    migration_178.downgrade()


# ── real-DB: matches on authorization_url, not provider_category/is_idp ─────


def _insert_provider(
    engine: Engine,
    *,
    provider_category: str,
    is_idp: bool,
    authorization_url: str,
    token_url: str,
    userinfo_url: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO oauth_providers
                    (provider_category, display_name, is_idp, client_id,
                     authorization_url, token_url, userinfo_url,
                     permitted_drift_seconds)
                VALUES
                    (:provider_category, 'Test Provider', :is_idp, 'client-id',
                     :authorization_url, :token_url, :userinfo_url, 60)
                """
            ),
            {
                'provider_category': provider_category,
                'is_idp': is_idp,
                'authorization_url': authorization_url,
                'token_url': token_url,
                'userinfo_url': userinfo_url,
            },
        )


def _provider_urls(engine: Engine) -> list[tuple[str, bool, str, str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT provider_category, is_idp, authorization_url, '
                'token_url, userinfo_url FROM oauth_providers '
                'ORDER BY id'
            )
        )
        return [tuple(row) for row in rows]


def test_upgrade_matches_by_broken_url_regardless_of_category(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    """The real bug, and the fix, must key off ``authorization_url`` alone."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '177'
    )

    # Broken row seeded by the old 168 -- not an IDP, to prove the repair
    # does not key off ``provider_category``/``is_idp`` at all.
    _insert_provider(
        engine,
        provider_category='github',
        is_idp=False,
        authorization_url='http://keycloak.keycloak/realms/allhands'
        '/protocol/openid-connect/auth',
        token_url='http://keycloak.keycloak/realms/allhands'
        '/protocol/openid-connect/token',
        userinfo_url='http://keycloak.keycloak/realms/allhands'
        '/protocol/openid-connect/userinfo',
    )
    # A legitimate enterprise_sso IDP row that was never broken -- proves the
    # repair does not blanket-overwrite every enterprise_sso/IDP row either.
    _insert_provider(
        engine,
        provider_category='enterprise_sso',
        is_idp=True,
        authorization_url='https://auth.example.com/realms/allhands'
        '/protocol/openid-connect/auth',
        token_url='https://auth.example.com/realms/allhands'
        '/protocol/openid-connect/token',
        userinfo_url='https://auth.example.com/realms/allhands'
        '/protocol/openid-connect/userinfo',
    )

    monkeypatch.setenv('AUTH_URL', 'https://auth.staging.all-hands.dev')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )

    rows = _provider_urls(engine)
    assert len(rows) == 2

    repaired = next(r for r in rows if r[0] == 'github')
    assert repaired[2:] == (
        'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/auth',
        'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/token',
        'https://auth.staging.all-hands.dev/realms/allhands'
        '/protocol/openid-connect/userinfo',
    )

    untouched = next(r for r in rows if r[0] == 'enterprise_sso')
    assert untouched[2:] == (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/auth',
        'https://auth.example.com/realms/allhands/protocol/openid-connect/token',
        'https://auth.example.com/realms/allhands/protocol/openid-connect/userinfo',
    )
