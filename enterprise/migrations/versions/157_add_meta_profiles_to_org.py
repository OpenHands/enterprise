"""Add meta_profiles column to org table.

Revision ID: 157
Revises: 156
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '157'
down_revision: Union[str, None] = '156'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('org', sa.Column('meta_profiles', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('org', 'meta_profiles')
