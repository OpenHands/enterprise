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

# The models the frontend previously hard-coded as free. Seeding them keeps the
# "Free" badge identical the moment the client switches to the DB-driven flag.
_FREE_MODELS = ('glm-5.2', 'deepseek-v4-flash', 'minimax-m2.7')

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

    # Seed free flags for existing rows only (no-op where the model is absent).
    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                UPDATE verified_models
                SET is_free = true, updated_at = CURRENT_TIMESTAMP
                WHERE model_name = :model_name AND provider = 'openhands'
                """
            ).bindparams(model_name=model_name)
        )

    # Seed the default; guarded by the partial unique index above.
    op.execute(
        sa.text(
            """
            UPDATE verified_models
            SET is_default = true, updated_at = CURRENT_TIMESTAMP
            WHERE model_name = :model_name AND provider = 'openhands'
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )


def downgrade() -> None:
    op.drop_index(_DEFAULT_INDEX, table_name='verified_models')
    op.drop_column('verified_models', 'is_default')
    op.drop_column('verified_models', 'is_free')
