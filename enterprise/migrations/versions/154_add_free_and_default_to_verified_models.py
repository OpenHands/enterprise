"""Add is_free and is_default to verified_models.

Revision ID: 154
Revises: 153
Create Date: 2026-08-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '154'
down_revision: Union[str, None] = '153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One default per provider is enforced by a partial unique index rather than a
# per-row flag alone, so the invariant cannot be violated by concurrent writes.
_DEFAULT_INDEX = 'uq_verified_model_default_per_provider'

# The model the frontend previously hard-coded as free. Seeding it keeps the
# "Free" badge identical the moment the client switches to the DB-driven flag.
_FREE_MODELS = ('deepseek-v4-flash',)

# Keep the database-seeded default aligned with the current code default.
_DEFAULT_MODEL = 'deepseek-v4-flash'


def upgrade() -> None:
    op.add_column(
        'verified_models',
        sa.Column(
            'is_free',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.add_column(
        'verified_models',
        sa.Column(
            'is_default',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.create_index(
        _DEFAULT_INDEX,
        'verified_models',
        ['provider'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )

    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                INSERT INTO verified_models (
                    model_name, provider, is_enabled, is_free, is_default
                )
                VALUES (:model_name, 'openhands', true, true, false)
                ON CONFLICT (model_name, provider) DO UPDATE
                SET is_enabled = true,
                    is_free = true,
                    updated_at = CURRENT_TIMESTAMP
                """
            ).bindparams(model_name=model_name)
        )

    op.execute(
        sa.text(
            """
            INSERT INTO verified_models (
                model_name, provider, is_enabled, is_free, is_default
            )
            VALUES (:model_name, 'openhands', true, true, true)
            ON CONFLICT (model_name, provider) DO UPDATE
            SET is_enabled = true,
                is_free = true,
                is_default = true,
                updated_at = CURRENT_TIMESTAMP
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )


def downgrade() -> None:
    op.drop_index(_DEFAULT_INDEX, table_name='verified_models')
    op.drop_column('verified_models', 'is_default')
    op.drop_column('verified_models', 'is_free')
