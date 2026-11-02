"""Add instance_id to instance_settings, to identify the install in usage reports.

Revision ID: 207
Revises: 206
Create Date: 2026-11-01
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '207'
down_revision: str | None = '206'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'instance_settings',
        sa.Column('instance_id', sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('instance_settings', 'instance_id')
