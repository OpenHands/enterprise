"""Add ``allow_match_by_email`` boolean column to the ``user`` table.

Supports the IDP swap-over flow (OHE-3293 / ALL-5978): when an installer
switches IDPs mid-install, the new IDP issues a different ``sub`` for every
user. ``allow_match_by_email`` is an opt-in, one-time identity-seeding flag
that lets the v2 login resolver bind the new IDP's ``sub`` to an existing
``User`` via email, then self-clears so the window is exactly one login wide.

Revision ID: 169
Revises: 168
Create Date: 2026-06-05 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '169'
down_revision: str | None = '168'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'allow_match_by_email',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('user', 'allow_match_by_email')
