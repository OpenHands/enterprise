"""Rename ``oauth_provider_users`` to ``auth_provider_users``.

Migration 186 renamed ``oauth_providers`` to ``auth_providers`` (the
provider-config table is no longer OAuth-specific now that it also holds
password/SAML rows), but left the identity-resolution table that references
it -- ``oauth_provider_users`` -- with its old name, which now reads
inconsistently next to ``auth_providers``/``auth_tokens``. This migration
finishes that rename: a plain ``ALTER TABLE ... RENAME`` (no column/data
changes -- ``oauth_provider_id``/``user_id``/``external_subject_id`` etc. are
untouched, and Postgres tracks the foreign key to ``auth_providers`` by OID,
not name, so it needs no changes either).

Postgres does not rename a table's indexes/constraints when the table itself
is renamed, so -- matching migration 186's handling of ``auth_providers``'s
own indexes -- the two named indexes on this table are dropped and
recreated under names that match the new table name:
``uq_oauth_provider_users_provider_subject`` ->
``uq_auth_provider_users_provider_subject`` and
``ix_oauth_provider_users_user_provider`` ->
``ix_auth_provider_users_user_provider``.

Revision ID: 187
Revises: 186
Create Date: 2026-10-14 00:00:00.000000
"""

from typing import Sequence

from alembic import op

revision: str = '187'
down_revision: str | None = '186'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TABLE = 'oauth_provider_users'
_NEW_TABLE = 'auth_provider_users'

_OLD_UNIQUE_CONSTRAINT_NAME = 'uq_oauth_provider_users_provider_subject'
_NEW_UNIQUE_CONSTRAINT_NAME = 'uq_auth_provider_users_provider_subject'

_OLD_INDEX_NAME = 'ix_oauth_provider_users_user_provider'
_NEW_INDEX_NAME = 'ix_auth_provider_users_user_provider'


def upgrade() -> None:
    op.rename_table(_OLD_TABLE, _NEW_TABLE)

    op.drop_constraint(_OLD_UNIQUE_CONSTRAINT_NAME, _NEW_TABLE, type_='unique')
    op.create_unique_constraint(
        _NEW_UNIQUE_CONSTRAINT_NAME,
        _NEW_TABLE,
        ['oauth_provider_id', 'external_subject_id'],
    )

    op.drop_index(_OLD_INDEX_NAME, table_name=_NEW_TABLE)
    op.create_index(_NEW_INDEX_NAME, _NEW_TABLE, ['user_id', 'oauth_provider_id'])


def downgrade() -> None:
    op.drop_index(_NEW_INDEX_NAME, table_name=_NEW_TABLE)
    op.create_index(_OLD_INDEX_NAME, _NEW_TABLE, ['user_id', 'oauth_provider_id'])

    op.drop_constraint(_NEW_UNIQUE_CONSTRAINT_NAME, _NEW_TABLE, type_='unique')
    op.create_unique_constraint(
        _OLD_UNIQUE_CONSTRAINT_NAME,
        _NEW_TABLE,
        ['oauth_provider_id', 'external_subject_id'],
    )

    op.rename_table(_NEW_TABLE, _OLD_TABLE)
