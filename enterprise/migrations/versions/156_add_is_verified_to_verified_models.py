"""Add is_verified to verified_models.

Revision ID: 156
Revises: 155
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '156'
down_revision: str | None = '155'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'verified_models',
        sa.Column(
            'is_verified',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )


def downgrade() -> None:
    op.drop_column('verified_models', 'is_verified')
