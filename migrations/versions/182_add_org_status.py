"""Add status column to org for Super Admin suspend/resume.

Revision ID: 182
Revises: 181
Create Date: 2026-09-23
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '182'
down_revision: str | None = '181'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org',
        sa.Column(
            'status',
            sa.String(),
            nullable=False,
            server_default='active',
        ),
    )


def downgrade() -> None:
    op.drop_column('org', 'status')
