"""Add ``is_disabled`` to the ``user`` table.

A Super Admin disables a user's sign-in, sessions and API keys with it,
whatever organizations the user belongs to (OHE-3478). Migration 162 dropped
the column of the same name that the reverted PR #181 had added.

Revision ID: 180
Revises: 179
Create Date: 2026-10-05 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '180'
down_revision: str | None = '179'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'is_disabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('user', 'is_disabled')
