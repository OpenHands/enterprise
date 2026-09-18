"""Add meta_profiles column to org table.

Revision ID: 164
Revises: 163
Create Date: 2026-09-01
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '164'
down_revision: str | None = '163'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('org', sa.Column('meta_profiles', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('org', 'meta_profiles')
