from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_upgrade_updates_enterprise_sso_idp_row(monkeypatch):
    bind = _run(
        monkeypatch,
        AUTH_URL='https://auth.staging.all-hands.dev',
        KEYCLOAK_REALM_NAME='allhands',
    )
    assert len(bind.writes) == 1
    sql, params = bind.writes[0]
    assert 'UPDATE oauth_providers' in sql
    assert params['provider_category_1'] == 'enterprise_sso'
    assert 'oauth_providers.is_idp IS true' in sql
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
