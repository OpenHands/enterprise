"""Prevent duplicate personal custom-secret rows (OHE-3342).

Revision ID: 167
Revises: 166
Create Date: 2026-09-23 00:00:00.000000

Adds a partial unique index on ``(keycloak_user_id, org_id, secret_name)``
restricted to personal secrets (``is_org_shared = false``). This stops the
org-scoped-secrets regression (PR #449) from leaving spurious duplicate
personal rows: the V1 write path used ``SaasSecretsStore.load()`` which
merges org-shared secrets into ``custom_secrets``, then ``store()``
re-inserted them as personal rows. The application fix (using a
personal-only ``load_personal``) prevents new duplicates; this migration
cleans up any duplicates that already exist and adds the constraint as
defense in depth.

A personal secret and an org-shared secret MAY share a name — the
runtime/listing layer dedups the org-shared one with a ``_2`` suffix — so
the index is scoped to personal rows only, leaving that coexistence intact.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '167'
down_revision: str | None = '166'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = 'uq_custom_secrets_personal_user_org_name'
# Personal secrets have a non-null keycloak_user_id (org-shared rows set it
# to the creator, but the index is scoped to is_org_shared = false so that
# doesn't matter here). NULL org_id rows (legacy / pre-org personal secrets)
# are included; Postgres treats NULLs as distinct in a unique index, which is
# the safe choice for legacy data.


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    # Clean up spurious duplicate personal rows left by the regression before
    # adding the unique index. For each (user, org, name) group with more than
    # one personal row, keep the most recently created one (highest id, since
    # id is an Identity sequence) and delete the rest. Org-shared rows are
    # untouched.
    op.execute(
        sa.text(
            """
            DELETE FROM custom_secrets
            WHERE id IN (
                SELECT id FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY keycloak_user_id, org_id, secret_name
                            ORDER BY id DESC
                        ) AS rn
                    FROM custom_secrets
                    WHERE is_org_shared = false
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )

    op.create_index(
        _INDEX_NAME,
        'custom_secrets',
        ['keycloak_user_id', 'org_id', 'secret_name'],
        unique=True,
        postgresql_where=sa.text('is_org_shared = false'),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name='custom_secrets')
