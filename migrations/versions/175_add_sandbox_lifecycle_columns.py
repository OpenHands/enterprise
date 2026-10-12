"""Add lifecycle columns to v1_remote_sandbox.

The app pauses and deletes the Docker, E2B and k8s agent-sandbox sandboxes it
manages. These columns record what it last did to each one, and when:

- ``lifecycle_state`` is ``running`` or ``paused``.
- ``state_changed_at`` is when the sandbox last started, resumed or paused.
- ``last_active_at`` is the latest agent activity the app has seen.

App servers still on the previous release insert rows without these columns
while a deploy rolls out, so each one has a server default. Existing rows read
as running since the migration.

Revision ID: 175
Revises: 174
Create Date: 2026-09-30 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox',
        sa.Column(
            'lifecycle_state', sa.String(), nullable=False, server_default='running'
        ),
    )
    op.add_column(
        'v1_remote_sandbox',
        sa.Column(
            'state_changed_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )
    op.add_column(
        'v1_remote_sandbox',
        sa.Column(
            'last_active_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )
    op.create_index(
        'ix_v1_remote_sandbox_backend_lifecycle_state',
        'v1_remote_sandbox',
        ['backend', 'lifecycle_state'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_v1_remote_sandbox_backend_lifecycle_state', table_name='v1_remote_sandbox'
    )
    op.drop_column('v1_remote_sandbox', 'last_active_at')
    op.drop_column('v1_remote_sandbox', 'state_changed_at')
    op.drop_column('v1_remote_sandbox', 'lifecycle_state')
