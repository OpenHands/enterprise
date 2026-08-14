"""Add git_clone_timeout setting to user settings.

Revision ID: 144
Revises: 143
Create Date: 2026-08-13 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '144'
down_revision: str | None = '143'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column('git_clone_timeout', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('user', 'git_clone_timeout')
