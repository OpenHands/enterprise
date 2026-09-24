"""Track successful budget alert destinations across retries.

Revision ID: 166
Revises: 165
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '166'
down_revision: str | None = '165'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_budget_threshold',
        sa.Column('delivery_state', sa.JSON(), nullable=False, server_default='{}'),
    )


def downgrade() -> None:
    op.drop_column('org_budget_threshold', 'delivery_state')
