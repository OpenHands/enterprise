"""Add enable_memory_context and memory_context columns to user table.

Supports enterprise persistent-memory: ``enable_memory_context`` gates
callback registration and sandbox injection; ``memory_context`` stores the
latest MEMORY.md content captured by the MemoryChangeCallbackProcessor.

Revision ID: 167
Revises: 166
Create Date: 2026-06-05 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '167'
down_revision: str | None = '166'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'enable_memory_context',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.add_column(
        'user',
        sa.Column('memory_context', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('user', 'memory_context')
    op.drop_column('user', 'enable_memory_context')
