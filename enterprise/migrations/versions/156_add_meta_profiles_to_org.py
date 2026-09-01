"""Add meta_profiles column to org table.

Revision ID: 156
Revises: 155
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "156"
down_revision: Union[str, None] = "155"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("org", sa.Column("meta_profiles", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("org", "meta_profiles")
