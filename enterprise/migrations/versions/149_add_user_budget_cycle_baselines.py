"""Add per-user budget cycle baselines.

Revision ID: 149
Revises: 148
Create Date: 2026-08-19 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '149'
down_revision: Union[str, None] = '148'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'org_budget_settings',
        sa.Column(
            'user_cycle_start_spend',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column('org_budget_settings', 'user_cycle_start_spend')
