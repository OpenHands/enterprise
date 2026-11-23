"""Add the org opt-in for conversation usage detail, off for every org.

Revision ID: 216
Revises: 215
Create Date: 2026-11-12
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '216'
down_revision: str | None = '215'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org',
        sa.Column(
            'enable_conversation_usage_detail',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('org', 'enable_conversation_usage_detail')
