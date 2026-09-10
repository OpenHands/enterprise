"""Add an instance-level disabled flag to users.

Revision ID: 157
Revises: 156
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '157'
down_revision: str | None = '156'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'is_disabled', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.alter_column('user', 'is_disabled', server_default=None)


def downgrade() -> None:
    op.drop_column('user', 'is_disabled')
