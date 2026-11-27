"""Record the image a sandbox was launched with.

Sandbox specs are keyed by config name, which stays the same when the config's
image is bumped, so the image has to be kept per sandbox to tell old from new.
Existing rows stay NULL; their spec ID is the image.

Revision ID: 226
Revises: 225
Create Date: 2026-11-25
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '226'
down_revision: str | None = '225'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox', sa.Column('sandbox_image', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('v1_remote_sandbox', 'sandbox_image')
