"""Record what started each sandbox.

Revision ID: 213
Revises: 212

``started_by`` holds ``SandboxStartedBy.value``: ``APP_CONVERSATION`` for a
sandbox the app server started to run a conversation, ``SANDBOX_API`` for one
a client started through ``POST /api/v1/sandboxes``. Conversation grouping
reuses only the former. Existing rows stay NULL: what started them was never
recorded, and grouping keeps treating them as it did before.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '213'
down_revision: str | None = '212'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox', sa.Column('started_by', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('v1_remote_sandbox', 'started_by')
