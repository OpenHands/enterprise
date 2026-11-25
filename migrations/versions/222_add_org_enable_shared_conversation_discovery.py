"""Add the org switch for discovering shared conversations, on for every org.

Revision ID: 222
Revises: 221
Create Date: 2026-11-23
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '222'
down_revision: str | None = '221'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org',
        sa.Column(
            'enable_shared_conversation_discovery',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column('org', 'enable_shared_conversation_discovery')
