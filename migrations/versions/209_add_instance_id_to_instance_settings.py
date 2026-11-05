"""Add instance_id to instance_settings, to identify the install in usage reports.

Revision ID: 209
Revises: 208
Create Date: 2026-11-01
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '209'
down_revision: str | None = '208'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'instance_settings',
        sa.Column('instance_id', sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('instance_settings', 'instance_id')
