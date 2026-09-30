"""Add scheduled_job_watermark for the CronJob to task queue handoff.

Revision ID: 175
Revises: 174
Create Date: 2026-09-30 00:00:00.000000

One row per scheduled job: the last occurrence known to have completed, and
how that was established. Procrastinate has no record of what a CronJob
already ran, so at cutover the operator records it here, and the task decides
per occurrence whether it still needs to run.

``supersedes_earlier`` records the assumption a single watermark depends on:
that a later run covers everything an earlier one would have done.
"""

from typing import Sequence

from alembic import op

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSIFICATIONS = ('completed', 'missed', 'skipped', 'unknown')


def upgrade() -> None:
    allowed = ', '.join(f"'{c}'" for c in CLASSIFICATIONS)
    op.execute(
        'CREATE TABLE scheduled_job_watermark ('
        ' job_name varchar(128) PRIMARY KEY,'
        ' occurrence timestamptz NOT NULL,'
        ' classification varchar(16) NOT NULL'
        f'  CHECK (classification IN ({allowed})),'
        ' supersedes_earlier boolean NOT NULL,'
        " source varchar(16) NOT NULL CHECK (source IN ('operator', 'task')),"
        ' recorded_by varchar(255),'
        ' recorded_at timestamptz NOT NULL DEFAULT now()'
        ')'
    )


def downgrade() -> None:
    op.execute('DROP TABLE scheduled_job_watermark')
