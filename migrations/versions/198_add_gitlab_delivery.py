"""Add gitlab_delivery, the GitLab event handler's durable dedupe (PLTF-3689).

One GitLab event can reach the handler through more than one hook that
authenticates, and GitLab queues each hook's deliveries separately, so the
copies can arrive minutes apart. The handler records a sha256 of each
delivery's raw body here before processing it and ignores a body it has
already recorded. Rows are kept for 7 days.

Revision ID: 198
Revises: 197
Create Date: 2026-10-20 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '198'
down_revision: str | None = '197'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'gitlab_delivery',
        sa.Column('body_sha256', sa.String(64), primary_key=True),
        sa.Column(
            'received_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )
    op.create_index(
        'ix_gitlab_delivery_received_at', 'gitlab_delivery', ['received_at']
    )


def downgrade() -> None:
    op.drop_index('ix_gitlab_delivery_received_at', table_name='gitlab_delivery')
    op.drop_table('gitlab_delivery')
