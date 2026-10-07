"""Add pull_requests to conversation_metadata.

Revision ID: 177
Revises: 176
Create Date: 2026-10-05 00:00:00.000000

One JSON item per PR that OpenHands opened from the conversation: number,
repository, git_provider and url. It supersedes ``pr_number``, which keeps only
the number. Existing rows stay NULL.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '177'
down_revision: str | None = '176'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'conversation_metadata',
        sa.Column('pull_requests', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('conversation_metadata', 'pull_requests')
