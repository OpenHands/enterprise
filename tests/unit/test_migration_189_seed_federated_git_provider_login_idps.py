"""Real-DB tests for migration 189:

- Seeding GitHub/GitLab/Bitbucket login IDPs routed through Keycloak,
  resolved from the environment.
- Deleting the legacy generic Keycloak ``auth_providers`` row.
- Rebranding a pre-fix integrated/password login row's ``title``/``color``.
"""

from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '189_seed_federated_git_provider_login_idps.py'
)
_spec = spec_from_file_location('migration_189', _MIGRATION_PATH)
assert _spec is not None and _spec.loader is not None
migration_189 = module_from_spec(_spec)
_spec.loader.exec_module(migration_189)

_ENV_VARS = (
    'GITHUB_APP_CLIENT_ID',
    'GITLAB_APP_CLIENT_ID',
    'BITBUCKET_APP_CLIENT_ID',
    'KEYCLOAK_CLIENT_ID',
    'KEYCLOAK_CLIENT_SECRET',
    'KEYCLOAK_REALM_NAME',
    'AUTH_URL',
    'AUTH_WEB_HOST',
    'WEB_HOST',
    'JWT_SECRET',
)


def _decrypt_with_fixed_secret(ciphertext: str, jwt_secret: str) -> str:
    """Decrypt a value encrypted (by the migration, running in its own
    ``run_alembic`` subprocess -- see ``postgres_testdb.run_alembic``) with
    ``storage.encrypt_utils.encrypt_value()`` under ``JWT_SECRET=<jwt_secret>``.

    Deliberately builds its own throwaway ``JwtService`` from that same
    ``JWT_SECRET`` instead of going through
    ``storage.encrypt_utils.get_jwt_service()``: that helper caches a
    process-wide singleton the first time anything in this pytest process
    calls it, which may happen in an earlier test with no ``JWT_SECRET`` set
    (falling back to a random/``.keys``-file key) -- a cache this test must
    not depend on or disturb for tests that run after it.
    """
    from openhands.app_server.services.jwt_service import JwtService
    from openhands.app_server.utils.encryption_key import (
        get_default_encryption_keys,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv('JWT_SECRET', jwt_secret)
        keys = get_default_encryption_keys(Path('/does-not-matter'))
    return JwtService(keys=keys).decrypt_value(ciphertext)


def _downgrade_to_188(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )


def _upgrade_to_189(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '189'
    )


def _insert_keycloak_idp_row(engine: Engine) -> None:
    """A legacy generic Keycloak row, as seeded by an older version of
    migration 168. Its ``details`` content doesn't matter here -- this
    migration only ever deletes it, never reads it."""
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
            {
                'details': json.dumps(
                    {'kind': 'OAuthProviderDetails', 'provider_type': 'enterprise_sso'}
                )
            },
        )


def _insert_password_login_row(
    engine: Engine, *, title: str = 'Password Login', color: str = '#000000'
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('PasswordProviderDetails', :title, true, :color, true,
                     :details)
                RETURNING id
                """
            ),
            {
                'title': title,
                'color': color,
                'details': json.dumps({'kind': 'PasswordProviderDetails'}),
            },
        ).scalar_one()


def _rows(engine: Engine, where: str = '') -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    f'SELECT id, title, color, icon, is_idp, kind, details '
                    f'FROM auth_providers {where} ORDER BY id'
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


def _federated_rows(engine: Engine) -> list[dict]:
    return _rows(engine, "WHERE details ->> 'provider_type' <> 'enterprise_sso'")


def _keycloak_rows(engine: Engine) -> list[dict]:
    return _rows(
        engine,
        "WHERE title = 'Keycloak' AND details ->> 'provider_type' = 'enterprise_sso'",
    )


def _password_login_rows(engine: Engine) -> list[dict]:
    return _rows(engine, "WHERE kind = 'PasswordProviderDetails'")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in _ENV_VARS:
        monkeypatch.delenv(key, raising=False)


def _set_keycloak_env(monkeypatch) -> None:
    monkeypatch.setenv('KEYCLOAK_CLIENT_ID', 'allhands')
    monkeypatch.setenv('KEYCLOAK_CLIENT_SECRET', 'kc-secret')
    monkeypatch.setenv('KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setenv('AUTH_URL', 'https://auth.example.com')


# ── seeding federated IDP rows from the environment ─────────────────────────


def test_upgrade_adds_a_row_per_configured_brand(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _set_keycloak_env(monkeypatch)
    # Fixes the key ``encrypt_value()`` uses for ``client_secret`` inside the
    # migration's own ``run_alembic`` subprocess, so this test can decrypt it
    # back deterministically -- see ``_decrypt_with_fixed_secret``.
    monkeypatch.setenv('JWT_SECRET', 'test-jwt-secret')
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
        assert row['details']['client_id'] == 'allhands'
        assert row['details']['authorization_url'] == (
            'https://auth.example.com/realms/allhands/protocol/openid-connect/auth'
            f'?kc_idp_hint={row["details"]["provider_type"]}'
        )
        assert (
            _decrypt_with_fixed_secret(
                row['details']['client_secret'], 'test-jwt-secret'
            )
            == 'kc-secret'
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
    _set_keycloak_env(monkeypatch)
    monkeypatch.setenv('BITBUCKET_APP_CLIENT_ID', 'bb-client-id')

    _upgrade_to_189(test_database)

    rows = _federated_rows(engine)
    assert len(rows) == 1
    assert rows[0]['details']['provider_type'] == 'bitbucket'
    assert rows[0]['title'] == 'Log in with Bitbucket'
    assert rows[0]['color'] == '#2684FF'
    assert rows[0]['details']['authorization_url'] == (
        'https://auth.example.com/realms/allhands/protocol/openid-connect/auth'
        '?kc_idp_hint=bitbucket'
    )


def test_with_idp_hint_uses_question_mark_when_url_has_no_query():
    assert (
        migration_189._with_idp_hint('https://auth.example.com/auth', 'github')
        == 'https://auth.example.com/auth?kc_idp_hint=github'
    )


def test_with_idp_hint_uses_ampersand_when_url_already_has_a_query():
    assert (
        migration_189._with_idp_hint(
            'https://auth.example.com/auth?prompt=login', 'github'
        )
        == 'https://auth.example.com/auth?prompt=login&kc_idp_hint=github'
    )


def test_with_idp_hint_passes_through_a_missing_url():
    assert migration_189._with_idp_hint(None, 'github') is None


def test_upgrade_noop_without_any_keycloak_client_id(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    # No KEYCLOAK_CLIENT_ID -- nothing to route federated login through,
    # even though a git-provider brand is configured.
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')

    _upgrade_to_189(test_database)

    assert _federated_rows(engine) == []


def test_upgrade_noop_without_any_git_provider_env(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _set_keycloak_env(monkeypatch)
    # No git-provider brand configured.

    _upgrade_to_189(test_database)

    assert _federated_rows(engine) == []


# ── removing the generic Keycloak row ───────────────────────────────────────


def test_upgrade_deletes_an_existing_keycloak_row(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)

    # Deletion happens regardless of whether any federated rows end up
    # seeded.
    _upgrade_to_189(test_database)

    assert _keycloak_rows(engine) == []


def test_upgrade_noop_without_an_existing_keycloak_row(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    # No pre-existing row -- a brand-new install (migration 168 no longer
    # seeds one). Deleting nothing must not raise.

    _upgrade_to_189(test_database)

    assert _keycloak_rows(engine) == []


def test_upgrade_does_not_touch_an_admin_added_enterprise_sso_row(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """A second, differently-titled ``enterprise_sso`` row (e.g. an
    admin-configured SAML/OIDC tenant) must survive."""
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, allow_registration, details)
                VALUES
                    ('OAuthProviderDetails', 'Corp SSO', true, '#111111', true,
                     :details)
                """
            ),
            {
                'details': json.dumps(
                    {'kind': 'OAuthProviderDetails', 'provider_type': 'enterprise_sso'}
                )
            },
        )
    _set_keycloak_env(monkeypatch)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')

    _upgrade_to_189(test_database)

    assert _keycloak_rows(engine) == []
    with engine.connect() as conn:
        remaining_titles = sorted(
            conn.execute(text('SELECT title FROM auth_providers')).scalars().all()
        )
    assert remaining_titles == ['Corp SSO', 'Log in with GitHub']


# ── rebranding the integrated/password login ────────────────────────────────


def test_upgrade_rebrands_a_pre_fix_password_login_row(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    provider_id = _insert_password_login_row(engine)

    _upgrade_to_189(test_database)

    with engine.connect() as conn:
        row = (
            conn.execute(
                text('SELECT title, color FROM auth_providers WHERE id = :id'),
                {'id': provider_id},
            )
            .mappings()
            .one()
        )
    assert row['title'] == 'Login with Password'
    assert row['color'] == '#03A956'


def test_upgrade_does_not_rebrand_a_customized_password_login_row(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    provider_id = _insert_password_login_row(
        engine, title='Corp Password Login', color='#123456'
    )

    _upgrade_to_189(test_database)

    with engine.connect() as conn:
        row = (
            conn.execute(
                text('SELECT title, color FROM auth_providers WHERE id = :id'),
                {'id': provider_id},
            )
            .mappings()
            .one()
        )
    assert row['title'] == 'Corp Password Login'
    assert row['color'] == '#123456'


# ── idempotency ──────────────────────────────────────────────────────────────


def test_upgrade_is_idempotent(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)
    _insert_password_login_row(engine)
    _set_keycloak_env(monkeypatch)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')

    _upgrade_to_189(test_database)
    assert len(_federated_rows(engine)) == 1
    assert _password_login_rows(engine)[0]['title'] == 'Login with Password'

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )
    _upgrade_to_189(test_database)

    assert len(_federated_rows(engine)) == 1
    assert _password_login_rows(engine)[0]['title'] == 'Login with Password'


# ── downgrade ────────────────────────────────────────────────────────────────


def test_downgrade_reverts_rebrand_removes_federated_rows_and_restores_keycloak(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _downgrade_to_188(test_database)
    _insert_keycloak_idp_row(engine)
    _insert_password_login_row(engine)
    _set_keycloak_env(monkeypatch)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'gh-client-id')
    monkeypatch.setenv('GITLAB_APP_CLIENT_ID', 'gl-client-id')
    monkeypatch.setenv('BITBUCKET_APP_CLIENT_ID', 'bb-client-id')

    _upgrade_to_189(test_database)
    assert len(_federated_rows(engine)) == 3
    assert _keycloak_rows(engine) == []
    assert _password_login_rows(engine)[0]['title'] == 'Login with Password'

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '188'
    )

    assert _federated_rows(engine) == []
    assert len(_keycloak_rows(engine)) == 1
    password_row = _password_login_rows(engine)[0]
    assert password_row['title'] == 'Password Login'
    assert password_row['color'] == '#000000'
