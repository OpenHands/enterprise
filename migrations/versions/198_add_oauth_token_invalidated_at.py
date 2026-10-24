"""Add ``invalidated_at`` to ``oauth_tokens``.

Set when the git provider rejects a linked token (a 401 from its API, or a
refused refresh), so the Integrations page can ask the user to reconnect
instead of showing a dead token as connected. ``NULL`` means not known to be
invalid; storing a new token clears it.

Revision ID: 198
Revises: 197
Create Date: 2026-10-23 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '198'
down_revision: str | None = '197'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'oauth_tokens',
        sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('oauth_tokens', 'invalidated_at')
