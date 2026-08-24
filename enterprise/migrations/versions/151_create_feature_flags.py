"""Create feature_flags and feature_flag_rules tables.

Revision ID: 151
Revises: 150
Create Date: 2025-06-05 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "151"
down_revision: Union[str, None] = "150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create feature_flags and feature_flag_rules tables."""
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False, primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_feature_flags_key"),
    )

    op.create_table(
        "feature_flag_rules",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False, primary_key=True),
        sa.Column("flag_id", sa.Integer(), nullable=False),
        sa.Column("effect", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("org_id", sa.String(), nullable=True),
        sa.Column("email_pattern", sa.String(), nullable=True),
        sa.Column("percentage", sa.Float(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["flag_id"], ["feature_flags.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "ix_feature_flag_rules_flag_id",
        "feature_flag_rules",
        ["flag_id"],
    )
    op.create_index(
        "ix_feature_flag_rules_effect",
        "feature_flag_rules",
        ["effect"],
    )


def downgrade() -> None:
    """Drop feature_flag_rules and feature_flags tables."""
    op.drop_index("ix_feature_flag_rules_effect", table_name="feature_flag_rules")
    op.drop_index("ix_feature_flag_rules_flag_id", table_name="feature_flag_rules")
    op.drop_table("feature_flag_rules")
    op.drop_table("feature_flags")
