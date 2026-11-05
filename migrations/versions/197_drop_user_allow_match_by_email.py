"""Drop ``allow_match_by_email`` from the ``user`` table.

Whether a login may be linked to an existing user by email is a property of
the login provider -- whether its email assertions are trusted -- so it is
decided by ``auth_providers.allow_match_by_email`` alone (OHE-3723). The
per-user flag (migrations 170 and 192) is no longer read or written.

Downgrade restores the column with every row ``false``: the per-user values
are not recoverable, and ``false`` is the column's original default.

Revision ID: 197
Revises: 196
Create Date: 2026-10-23 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '197'
down_revision: str | None = '196'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('user', 'allow_match_by_email')


def downgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'allow_match_by_email',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
