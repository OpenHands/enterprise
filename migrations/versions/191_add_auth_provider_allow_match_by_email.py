"""Add ``allow_match_by_email`` to ``auth_providers``.

Seeds ``user.allow_match_by_email`` (migration 170) for users created through
the OAuth v2 login path: when a login through an ``auth_providers`` row mints
a brand-new user (see
``server.auth.provider_identity.resolve_or_create_user``), the new user's
``allow_match_by_email`` is initialised from this column. Defaults to
``True`` for every existing and new row.

Revision ID: 191
Revises: 190
Create Date: 2026-10-16 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '191'
down_revision: str | None = '190'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'auth_providers',
        sa.Column(
            'allow_match_by_email',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )


def downgrade() -> None:
    op.drop_column('auth_providers', 'allow_match_by_email')
