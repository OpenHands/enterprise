"""Add canonical versioned agent settings to organization members."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '142'
down_revision: Union[str, None] = '141'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This is intentionally nullable and not eagerly backfilled. Application
    # reads continue to compose the rollback-safe legacy columns, including
    # their historical MCP schema, until the member next saves settings.
    op.add_column(
        'org_member',
        sa.Column('agent_settings', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # The application dual-writes the legacy columns throughout rollout, so
    # downgrading only needs to remove the expanded representation.
    op.drop_column('org_member', 'agent_settings')
