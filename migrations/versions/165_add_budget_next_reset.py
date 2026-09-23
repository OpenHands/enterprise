"""Persist the first reset after a budget reset-day edit.

Revision ID: 165
Revises: 164
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '165'
down_revision: str | None = '164'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL preserves the existing calendar schedule until its first edit.
    op.add_column(
        'org_budget_settings',
        sa.Column('next_reset_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_budget_settings', 'next_reset_at')
