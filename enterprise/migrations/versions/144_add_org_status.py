"""Add status column to org for Super Admin suspend/resume.

Revision ID: 144
Revises: 143
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '144'
down_revision: Union[str, None] = '143'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
