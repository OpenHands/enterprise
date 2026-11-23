"""Add the admin's instructions shown to users who reach a budget.

Revision ID: 220
Revises: 219
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '220'
down_revision: str | None = '219'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_budget_settings',
        sa.Column('limit_reached_instructions', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_budget_settings', 'limit_reached_instructions')
