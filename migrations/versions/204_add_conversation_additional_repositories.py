"""Add additional_repositories and pull_requests to conversation_metadata.

``additional_repositories`` holds the repositories cloned beside the main one
and their clone status; ``pull_requests`` records the repository of each pull
request a conversation opened.

Revision ID: 204
Revises: 203
Create Date: 2026-10-27 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '204'
down_revision: str | None = '203'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'conversation_metadata',
        sa.Column('additional_repositories', sa.JSON(), nullable=True),
    )
    op.add_column(
        'conversation_metadata',
        sa.Column('pull_requests', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('conversation_metadata', 'pull_requests')
    op.drop_column('conversation_metadata', 'additional_repositories')
