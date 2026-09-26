"""Add meta_profiles column to org table.

Revision ID: 165
Revises: 164
Create Date: 2026-09-01
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '165'
down_revision: str | None = '164'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('org', sa.Column('meta_profiles', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('org', 'meta_profiles')
