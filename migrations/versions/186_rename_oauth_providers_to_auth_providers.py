"""Rename ``oauth_providers`` to ``auth_providers`` and collapse its
protocol-specific columns into a single polymorphic ``details`` JSON column
(with its secret fields individually encrypted).

The OAuth v2 refactor (migrations 168/176/178/179) already showed the seams
of a flat ``oauth_providers`` table: every row needed the same six OAuth2
columns (``client_id``/``client_secret``/``authorization_url``/``token_url``/
``userinfo_url``/``scopes``) except the integrated/password IDP, which left
them all ``NULL`` and was told apart from a real OAuth2 row only by a
``provider_category='integrated_idp'`` sentinel string compared in several
places (``server/routes/oauth_v2.py``, ``server/routes/idp.py``). Adding a
genuinely different protocol (SAML) on top would mean a third set of
NULL-for-everyone-else columns and a third sentinel string.

This migration renames the table (``ALTER TABLE ... RENAME`` preserves row
ids, so ``oauth_provider_users.oauth_provider_id`` / ``oauth_tokens.
oauth_provider_id`` need no changes -- Postgres tracks foreign keys by OID,
not name) and replaces the flat OAuth2 columns with:

- ``kind`` -- a plain, unencrypted string mirroring
  ``type(details).__name__`` (``'OAuthProviderDetails'`` /
  ``'PasswordProviderDetails'`` / ``'SamlProviderDetails'``), kept in sync by
  ``storage.auth_provider.AuthProvider._sync_kind`` so it can never drift
  from ``details``. Exists purely so rows can be filtered by protocol
  without decrypting every row.
- ``title`` -- renamed from ``display_name``; otherwise unchanged.
- ``details`` -- a ``storage.auth_provider_details.AuthProviderDetails``
  discriminated union, stored as a *plain* JSON column (readable straight out
  of the database) rather than an ``EncryptedJSON``-style whole-blob boundary.
  Only its secret field(s) -- ``OAuthProviderDetails.client_secret`` here --
  are individually encrypted, via that field's own ``field_serializer`` (see
  ``storage.auth_provider_details`` and
  ``storage.encrypt_utils.create_json_with_encrypted_secrets_type_decorator``).
  ``details`` carries whatever the protocol in ``kind`` actually needs -- the
  six old OAuth2 columns for an ``OAuthProviderDetails`` row (including a new
  ``provider_type`` field that replaces the brand half of the old
  ``provider_category`` -- see below), nothing extra for
  ``PasswordProviderDetails``.

``provider_category`` used to do two jobs: which *protocol* a row used
(``'integrated_idp'`` vs. everything else) and, for OAuth2 rows, which
*brand* it was (``'github'``, ``'gitlab'``, ..., ``'enterprise_sso'``). The
former is now ``kind``. The latter becomes
``OAuthProviderDetails.provider_type`` -- it is not a property every
``auth_providers`` row has (the password/SAML kinds have no "brand"), so it
moved into the OAuth-specific side of ``details`` rather than staying a
top-level column. The brand-based lookup this enabled
(``OAuthProviderStore.get_first_by_category`` /
``GET /oauth/{provider_type}-login``) is deleted outright, not ported: it
was only ever a convenience alias for a known provider id and is not load
-bearing for anything else. ``provider_category``'s *protocol* role --
telling the integrated/password row apart from a real IDP -- survives as
``AuthProviderStore.get_first_by_kind(PasswordProviderDetails.__name__)``,
``server.routes.idp``'s one remaining caller.

This migration assumes every existing ``provider_category`` value seeded so
far (``'enterprise_sso'``, ``'github'``, ``'gitlab'``, ``'bitbucket'``,
``'bitbucket_data_center'``, ``'azure_devops'``, or ``'integrated_idp'``) is
either a valid ``ProviderType`` or the integrated-IDP sentinel -- true for
every row migrations 168/179 can have seeded. An admin-created row with some
other free-form ``provider_category`` (the admin API never validated it
against the enum) would fail loudly here rather than be silently coerced;
per OHE-3608 no such rows exist outside dev/eval installs with the feature
flag on, which is what makes this rename-in-place viable instead of a slower
additive migration.

Migration 185's "at most one integrated/password IDP row, and it is always
an IDP" guard (``uq_oauth_providers_one_integrated_idp`` /
``ck_oauth_providers_integrated_idp_is_idp``, both defined on
``provider_category``) is implicitly dropped by this migration's
``DROP COLUMN provider_category`` (Postgres auto-drops indexes/constraints
that involve a dropped column) and re-created here on the new ``kind``/
``is_idp`` columns as ``uq_auth_providers_one_password_provider`` /
``ck_auth_providers_password_provider_is_idp`` -- same invariant, same
rationale, just keyed off ``kind == 'PasswordProviderDetails'`` instead of
``provider_category == 'integrated_idp'``.

Revision ID: 186
Revises: 185
Create Date: 2026-10-12 00:00:00.000000
"""

import json
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '186'
down_revision: str | None = '185'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors storage.auth_provider_details.{OAuthProviderDetails,
# PasswordProviderDetails}.__name__. Duplicated (not imported) so this
# migration's data shape stays fixed in time even if those classes change
# later -- same convention as migration 179's duplicated
# INTEGRATED_IDP_CATEGORY.
_OAUTH_KIND = 'OAuthProviderDetails'
_PASSWORD_KIND = 'PasswordProviderDetails'
_INTEGRATED_IDP_CATEGORY = 'integrated_idp'

# Migration 185's guard on the old flat schema, re-created here on `kind`/
# `is_idp` -- see the comment in `upgrade()` below.
UNIQUE_PASSWORD_PROVIDER_INDEX_NAME = 'uq_auth_providers_one_password_provider'
PASSWORD_PROVIDER_IS_IDP_CHECK_NAME = 'ck_auth_providers_password_provider_is_idp'

# Migration 185's original names, for the downgrade path.
_OLD_UNIQUE_INDEX_NAME = 'uq_oauth_providers_one_integrated_idp'
_OLD_CHECK_CONSTRAINT_NAME = 'ck_oauth_providers_integrated_idp_is_idp'


def _dump_details(details) -> str:
    """JSON-serialize an ``AuthProviderDetails`` instance for the ``details``
    column, encrypting its own secret field(s) along the way.

    Delegates to the real app machinery (the same ``context={'for_storage':
    True}`` dump the ``details`` column's own type decorator uses -- see
    ``storage.encrypt_utils.create_json_with_encrypted_secrets_type_decorator``)
    rather than reimplementing it, since crypto helpers -- unlike pure
    sentinel values -- must exactly match runtime behavior. Returned as a
    JSON string (rather than the raw dict) to match how other raw-SQL
    migrations in this file write a JSON column (e.g. ``scopes`` below) --
    Postgres casts a valid JSON text literal to the column's ``json`` type.
    """
    from pydantic import TypeAdapter

    from storage.auth_provider_details import AuthProviderDetails

    type_adapter: TypeAdapter = TypeAdapter(AuthProviderDetails)
    dumped = type_adapter.dump_python(
        details, mode='json', context={'for_storage': True}
    )
    return json.dumps(dumped)


def _load_details(raw):
    """Reverse of ``_dump_details`` -- decrypts the secret field(s) back."""
    from pydantic import TypeAdapter

    from storage.auth_provider_details import AuthProviderDetails

    if isinstance(raw, str):
        raw = json.loads(raw)
    type_adapter: TypeAdapter = TypeAdapter(AuthProviderDetails)
    return type_adapter.validate_python(raw, context={'from_storage': True})


def _decrypt_client_secret(raw: str | None) -> str | None:
    """Reverse of migration 168's ``_encrypt_secret`` for one old row."""
    if not raw:
        return None
    from storage.encrypt_utils import decrypt_value

    return json.loads(decrypt_value(raw)).get('v')


def _backfill_details(bind) -> None:
    from openhands.app_server.integrations.service_types import ProviderType
    from storage.auth_provider_details import (
        OAuthProviderDetails,
        PasswordProviderDetails,
    )

    rows = (
        bind.execute(
            sa.text(
                """
            SELECT id, provider_category, display_name, client_id,
                   client_secret, authorization_url, token_url, userinfo_url,
                   scopes, permitted_drift_seconds
            FROM auth_providers
            """
            )
        )
        .mappings()
        .all()
    )

    for row in rows:
        category = row['provider_category']
        if category == _INTEGRATED_IDP_CATEGORY:
            kind = _PASSWORD_KIND
            details = PasswordProviderDetails()
        else:
            kind = _OAUTH_KIND
            scopes = row['scopes']
            if isinstance(scopes, str):
                scopes = json.loads(scopes)
            details = OAuthProviderDetails(
                provider_type=ProviderType(category),
                client_id=row['client_id'],
                client_secret=_decrypt_client_secret(row['client_secret']),
                authorization_url=row['authorization_url'],
                token_url=row['token_url'],
                userinfo_url=row['userinfo_url'],
                scopes=scopes,
                permitted_drift_seconds=row['permitted_drift_seconds'],
            )
        bind.execute(
            sa.text(
                'UPDATE auth_providers '
                'SET kind = :kind, title = :title, details = :details '
                'WHERE id = :id'
            ),
            {
                'kind': kind,
                'title': row['display_name'],
                'details': _dump_details(details),
                'id': row['id'],
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    inspector = sa.inspect(bind)
    if not inspector.has_table('oauth_providers'):
        # Should not happen (migration 168 creates it) but guards against
        # running this out of order / on a fresh install that never had it.
        return

    op.rename_table('oauth_providers', 'auth_providers')
    op.drop_index('ix_oauth_providers_provider_category', table_name='auth_providers')

    op.add_column('auth_providers', sa.Column('kind', sa.String(128), nullable=True))
    op.add_column('auth_providers', sa.Column('title', sa.String(128), nullable=True))
    op.add_column('auth_providers', sa.Column('details', sa.JSON(), nullable=True))

    _backfill_details(bind)

    op.alter_column('auth_providers', 'kind', nullable=False)
    op.alter_column('auth_providers', 'title', nullable=False)
    op.alter_column('auth_providers', 'details', nullable=False)

    op.drop_column('auth_providers', 'provider_category')
    op.drop_column('auth_providers', 'display_name')
    op.drop_column('auth_providers', 'client_id')
    op.drop_column('auth_providers', 'client_secret')
    op.drop_column('auth_providers', 'authorization_url')
    op.drop_column('auth_providers', 'token_url')
    op.drop_column('auth_providers', 'userinfo_url')
    op.drop_column('auth_providers', 'scopes')
    op.drop_column('auth_providers', 'permitted_drift_seconds')

    op.create_index('ix_auth_providers_kind', 'auth_providers', ['kind'])

    # Dropping `provider_category` above auto-dropped migration 185's
    # `uq_oauth_providers_one_integrated_idp` / `ck_oauth_providers_
    # integrated_idp_is_idp` (Postgres drops indexes/constraints involving a
    # dropped column). Re-establish the same invariant -- at most one
    # password-IDP row, and it is always an IDP -- on the new `kind`/`is_idp`
    # columns so the DB keeps enforcing it independently of the admin API's
    # own `_validate_password_idp_invariant` check.
    op.create_index(
        UNIQUE_PASSWORD_PROVIDER_INDEX_NAME,
        'auth_providers',
        ['kind'],
        unique=True,
        postgresql_where=sa.text(f"kind = '{_PASSWORD_KIND}'"),
    )
    op.create_check_constraint(
        PASSWORD_PROVIDER_IS_IDP_CHECK_NAME,
        'auth_providers',
        f"kind <> '{_PASSWORD_KIND}' OR is_idp",
    )


def _restore_flat_columns(bind) -> None:
    rows = (
        bind.execute(sa.text('SELECT id, kind, title, details FROM auth_providers'))
        .mappings()
        .all()
    )

    for row in rows:
        if row['kind'] == _PASSWORD_KIND:
            bind.execute(
                sa.text(
                    """
                    UPDATE auth_providers SET
                        provider_category = :category,
                        display_name = :display_name,
                        client_id = :client_id,
                        permitted_drift_seconds = 60
                    WHERE id = :id
                    """
                ),
                {
                    'category': _INTEGRATED_IDP_CATEGORY,
                    'display_name': row['title'],
                    'client_id': 'integrated-idp',
                    'id': row['id'],
                },
            )
            continue

        details = _load_details(row['details'])
        client_secret = (
            details.client_secret.get_secret_value() if details.client_secret else None
        )
        bind.execute(
            sa.text(
                """
                UPDATE auth_providers SET
                    provider_category = :category,
                    display_name = :display_name,
                    client_id = :client_id,
                    client_secret = :client_secret,
                    authorization_url = :authorization_url,
                    token_url = :token_url,
                    userinfo_url = :userinfo_url,
                    scopes = :scopes,
                    permitted_drift_seconds = :permitted_drift_seconds
                WHERE id = :id
                """
            ),
            {
                'category': details.provider_type.value,
                'display_name': row['title'],
                'client_id': details.client_id,
                'client_secret': (
                    _encrypt_legacy_client_secret(client_secret)
                    if client_secret
                    else None
                ),
                'authorization_url': details.authorization_url,
                'token_url': details.token_url,
                'userinfo_url': details.userinfo_url,
                'scopes': (
                    json.dumps(details.scopes) if details.scopes is not None else None
                ),
                'permitted_drift_seconds': details.permitted_drift_seconds,
                'id': row['id'],
            },
        )


def _encrypt_legacy_client_secret(plaintext: str) -> str:
    """Mirrors migration 168's ``_encrypt_secret`` for the downgrade path."""
    from storage.encrypt_utils import encrypt_value

    return encrypt_value(json.dumps({'v': plaintext}))


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_constraint(
        PASSWORD_PROVIDER_IS_IDP_CHECK_NAME, 'auth_providers', type_='check'
    )
    op.drop_index(UNIQUE_PASSWORD_PROVIDER_INDEX_NAME, table_name='auth_providers')
    op.drop_index('ix_auth_providers_kind', table_name='auth_providers')

    op.add_column(
        'auth_providers', sa.Column('provider_category', sa.String(64), nullable=True)
    )
    op.add_column(
        'auth_providers', sa.Column('display_name', sa.String(128), nullable=True)
    )
    op.add_column(
        'auth_providers', sa.Column('client_id', sa.String(255), nullable=True)
    )
    op.add_column(
        'auth_providers', sa.Column('client_secret', sa.String(), nullable=True)
    )
    op.add_column(
        'auth_providers',
        sa.Column('authorization_url', sa.String(2048), nullable=True),
    )
    op.add_column(
        'auth_providers', sa.Column('token_url', sa.String(2048), nullable=True)
    )
    op.add_column(
        'auth_providers', sa.Column('userinfo_url', sa.String(2048), nullable=True)
    )
    op.add_column('auth_providers', sa.Column('scopes', sa.JSON(), nullable=True))
    op.add_column(
        'auth_providers',
        sa.Column('permitted_drift_seconds', sa.Integer(), nullable=True),
    )

    _restore_flat_columns(bind)

    op.alter_column('auth_providers', 'provider_category', nullable=False)
    op.alter_column('auth_providers', 'display_name', nullable=False)
    op.alter_column('auth_providers', 'client_id', nullable=False)
    op.alter_column('auth_providers', 'permitted_drift_seconds', nullable=False)

    op.drop_column('auth_providers', 'details')
    op.drop_column('auth_providers', 'title')
    op.drop_column('auth_providers', 'kind')

    op.rename_table('auth_providers', 'oauth_providers')
    op.create_index(
        'ix_oauth_providers_provider_category',
        'oauth_providers',
        ['provider_category'],
    )
    # Restore migration 185's guard exactly as it existed before this
    # migration's `upgrade()` ran (dropping `provider_category` there
    # auto-dropped these, same as any other index/constraint on a dropped
    # column).
    op.create_index(
        _OLD_UNIQUE_INDEX_NAME,
        'oauth_providers',
        ['provider_category'],
        unique=True,
        postgresql_where=sa.text(f"provider_category = '{_INTEGRATED_IDP_CATEGORY}'"),
    )
    op.create_check_constraint(
        _OLD_CHECK_CONSTRAINT_NAME,
        'oauth_providers',
        f"provider_category <> '{_INTEGRATED_IDP_CATEGORY}' OR is_idp",
    )
