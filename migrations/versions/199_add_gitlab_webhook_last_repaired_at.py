"""Add gitlab_webhook.last_repaired_at for the installer's repair sweep (PLTF-3689).

Each installer run sweeps a few installed rows for hooks with our URL that
can never authenticate (stale identities and legacy hooks), oldest sweep
first. NULL means never swept.

Revision ID: 199
Revises: 198
Create Date: 2026-10-20 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '199'
down_revision: str | None = '198'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'gitlab_webhook',
        sa.Column('last_repaired_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_gitlab_webhook_last_repaired_at', 'gitlab_webhook', ['last_repaired_at']
    )


def downgrade() -> None:
    op.drop_index('ix_gitlab_webhook_last_repaired_at', table_name='gitlab_webhook')
    op.drop_column('gitlab_webhook', 'last_repaired_at')
