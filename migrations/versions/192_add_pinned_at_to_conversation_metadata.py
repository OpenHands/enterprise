"""Add ``pinned_at`` to ``conversation_metadata``.

Lets a user pin a conversation. ``pinned_at`` is the time it was pinned (null
when not pinned), so clients can list pinned conversations newest-pin first.
Existing rows stay unpinned.

Revision ID: 192
Revises: 191
Create Date: 2026-10-16 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '192'
down_revision: str | None = '191'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'conversation_metadata',
        sa.Column('pinned_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('conversation_metadata', 'pinned_at')
