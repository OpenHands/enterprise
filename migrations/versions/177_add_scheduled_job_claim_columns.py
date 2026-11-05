"""Add per-run claim columns to maintenance_tasks and gitlab_webhook (PLTF-3689).

The budget/maintenance runner and the GitLab webhook installer claim their rows
so two runs of a job cannot work on the same row (#552, #623). A claim is the
run's id and when it was taken; every later write is conditional on the run
still holding it. ``gitlab_webhook`` also gets a reinstall request counter and
the generation the installer last served: a row needs a reinstall while the
request is ahead. Nothing reads the columns until the claim code ships; the new
counters start equal, so no row needs a reinstall.

Revision ID: 177
Revises: 176
Create Date: 2026-10-07 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = '177'
down_revision: str | None = '176'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLAIMED_TABLES = ('maintenance_tasks', 'gitlab_webhook')
GENERATIONS = ('reinstall_requested_gen', 'reinstall_done_gen')


def upgrade() -> None:
    for table in CLAIMED_TABLES:
        op.add_column(table, sa.Column('claim_run_id', UUID(as_uuid=True)))
        op.add_column(table, sa.Column('claimed_at', sa.DateTime(timezone=True)))
    for column in GENERATIONS:
        op.add_column(
            'gitlab_webhook',
            sa.Column(column, sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    for column in GENERATIONS:
        op.drop_column('gitlab_webhook', column)
    for table in CLAIMED_TABLES:
        op.drop_column(table, 'claimed_at')
        op.drop_column(table, 'claim_run_id')
