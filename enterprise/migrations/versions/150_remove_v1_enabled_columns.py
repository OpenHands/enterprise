"""Remove obsolete V1 feature flag columns.

Revision ID: 150
Revises: 149
Create Date: 2026-08-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "150"
down_revision: Union[str, None] = "149"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("user_settings", "v1_enabled")
    op.drop_column("org", "v1_enabled")
    op.drop_column("slack_conversation", "v1_enabled")


def downgrade() -> None:
    op.add_column(
        "slack_conversation", sa.Column("v1_enabled", sa.Boolean(), nullable=True)
    )
    op.add_column("org", sa.Column("v1_enabled", sa.Boolean(), nullable=True))
    op.add_column("user_settings", sa.Column("v1_enabled", sa.Boolean(), nullable=True))
