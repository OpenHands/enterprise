"""Real-DB tests for migration 186 (``oauth_providers`` -> ``auth_providers``).

Unlike the lighter migrations (168/178/179), 186 reshapes column-for-column
data (flat OAuth2 columns -> polymorphic ``details`` with its secret
field(s) individually encrypted), so these tests run the real migration
against a real database (via ``postgres_testdb.run_alembic``) rather than
faking the bind -- a fake bind would have to reimplement the SELECT/UPDATE
round-trip this migration depends on, which is exactly what would go
untested.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _read_details(raw) -> dict:
    """``details`` is a plain JSON column -- depending on the DBAPI driver,
    a raw ``text()`` SELECT may hand back an already-parsed ``dict`` or the
    JSON text itself; normalize either to a ``dict``."""
    return json.loads(raw) if isinstance(raw, str) else raw


def _decrypt_secret_field(raw: str) -> str:
    from storage.encrypt_utils import decrypt_value

    return decrypt_value(raw)


def _insert_legacy_provider(
    engine: Engine,
    *,
    provider_category: str,
    display_name: str,
    is_idp: bool,
    client_id: str,
    client_secret: str | None = None,
    authorization_url: str | None = None,
    token_url: str | None = None,
    userinfo_url: str | None = None,
    scopes: list[str] | None = None,
) -> int:
    from storage.encrypt_utils import encrypt_value

    encrypted_secret = (
        encrypt_value(json.dumps({'v': client_secret}))
        if client_secret is not None
        else None
    )
    with engine.begin() as conn:
        row_id = conn.execute(
            text(
                """
                INSERT INTO oauth_providers
                    (provider_category, display_name, is_idp, client_id,
                     client_secret, authorization_url, token_url, userinfo_url,
                     scopes, permitted_drift_seconds)
                VALUES
                    (:provider_category, :display_name, :is_idp, :client_id,
                     :client_secret, :authorization_url, :token_url,
                     :userinfo_url, :scopes, 60)
                RETURNING id
                """
            ),
            {
                'provider_category': provider_category,
                'display_name': display_name,
                'is_idp': is_idp,
                'client_id': client_id,
                'client_secret': encrypted_secret,
                'authorization_url': authorization_url,
                'token_url': token_url,
                'userinfo_url': userinfo_url,
                'scopes': json.dumps(scopes) if scopes is not None else None,
            },
        ).scalar_one()
    return row_id


def test_upgrade_reshapes_oauth2_row_into_encrypted_details(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )
    github_id = _insert_legacy_provider(
        engine,
        provider_category='github',
        display_name='GitHub',
        is_idp=False,
        client_id='gh-client',
        client_secret='gh-secret',
        authorization_url='https://github.com/login/oauth/authorize',
        token_url='https://github.com/login/oauth/access_token',
        userinfo_url='https://api.github.com/user',
        scopes=['repo', 'read:user'],
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '186'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT kind, title, is_idp, details FROM auth_providers '
                    'WHERE id = :id'
                ),
                {'id': github_id},
            )
            .mappings()
            .one()
        )

    assert row['kind'] == 'OAuthProviderDetails'
    assert row['title'] == 'GitHub'
    assert row['is_idp'] is False

    details = _read_details(row['details'])
    assert details['kind'] == 'OAuthProviderDetails'
    assert details['provider_type'] == 'github'
    assert details['client_id'] == 'gh-client'
    # Only client_secret is encrypted -- everything else in `details` is
    # plain, human-readable JSON, unlike the old whole-blob-encrypted design.
    assert details['client_secret'] != 'gh-secret'
    assert _decrypt_secret_field(details['client_secret']) == 'gh-secret'
    assert details['authorization_url'] == 'https://github.com/login/oauth/authorize'
    assert details['token_url'] == 'https://github.com/login/oauth/access_token'
    assert details['userinfo_url'] == 'https://api.github.com/user'
    assert details['scopes'] == ['repo', 'read:user']
    assert details['permitted_drift_seconds'] == 60


def test_upgrade_reshapes_integrated_idp_row_into_password_details(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )
    integrated_id = _insert_legacy_provider(
        engine,
        provider_category='integrated_idp',
        display_name='Integrated IDP',
        is_idp=True,
        client_id='integrated-idp',
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '186'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT kind, title, is_idp, details FROM auth_providers '
                    'WHERE id = :id'
                ),
                {'id': integrated_id},
            )
            .mappings()
            .one()
        )

    assert row['kind'] == 'PasswordProviderDetails'
    assert row['title'] == 'Integrated IDP'
    assert row['is_idp'] is True
    assert _read_details(row['details']) == {'kind': 'PasswordProviderDetails'}


def test_upgrade_preserves_ids_so_foreign_keys_still_resolve(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """Renaming the table must not renumber rows: ``oauth_provider_users``/
    ``oauth_tokens`` reference it by id, created against whatever id this row
    had before migration 186 ran."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )
    provider_id = _insert_legacy_provider(
        engine,
        provider_category='gitlab',
        display_name='GitLab',
        is_idp=False,
        client_id='gl-client',
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '186'
    )

    with engine.connect() as conn:
        found_id = conn.execute(
            text('SELECT id FROM auth_providers WHERE id = :id'),
            {'id': provider_id},
        ).scalar_one_or_none()
    assert found_id == provider_id


def test_downgrade_restores_flat_oauth2_columns(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )
    github_id = _insert_legacy_provider(
        engine,
        provider_category='github',
        display_name='GitHub',
        is_idp=False,
        client_id='gh-client',
        client_secret='gh-secret',
        authorization_url='https://github.com/login/oauth/authorize',
        token_url='https://github.com/login/oauth/access_token',
        userinfo_url='https://api.github.com/user',
        scopes=['repo'],
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '186'
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT provider_category, display_name, is_idp, client_id, '
                    'client_secret, authorization_url, token_url, userinfo_url, '
                    'scopes, permitted_drift_seconds FROM oauth_providers '
                    'WHERE id = :id'
                ),
                {'id': github_id},
            )
            .mappings()
            .one()
        )

    assert row['provider_category'] == 'github'
    assert row['display_name'] == 'GitHub'
    assert row['is_idp'] is False
    assert row['client_id'] == 'gh-client'
    assert json.loads(_decrypt_secret(row['client_secret']))['v'] == 'gh-secret'
    assert row['authorization_url'] == 'https://github.com/login/oauth/authorize'
    assert row['token_url'] == 'https://github.com/login/oauth/access_token'
    assert row['userinfo_url'] == 'https://api.github.com/user'
    assert row['permitted_drift_seconds'] == 60


def _decrypt_secret(raw: str) -> str:
    from storage.encrypt_utils import decrypt_value

    return decrypt_value(raw)


def test_downgrade_restores_integrated_idp_row(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )
    integrated_id = _insert_legacy_provider(
        engine,
        provider_category='integrated_idp',
        display_name='Integrated IDP',
        is_idp=True,
        client_id='integrated-idp',
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '186'
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '185'
    )

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    'SELECT provider_category, display_name, is_idp '
                    'FROM oauth_providers WHERE id = :id'
                ),
                {'id': integrated_id},
            )
            .mappings()
            .one()
        )

    assert row['provider_category'] == 'integrated_idp'
    assert row['display_name'] == 'Integrated IDP'
    assert row['is_idp'] is True
