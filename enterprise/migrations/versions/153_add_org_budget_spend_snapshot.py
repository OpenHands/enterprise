"""Add last-known-good LiteLLM budget spend snapshot.

Revision ID: 153
Revises: 152
Create Date: 2026-08-25 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '153'
down_revision: Union[str, None] = '152'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'org_budget_settings',
        sa.Column(
            'litellm_last_spend_snapshot_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'org_budget_settings',
        sa.Column('litellm_last_team_spend', sa.Float(), nullable=True),
    )
    op.add_column(
        'org_budget_settings',
        sa.Column(
            'litellm_last_member_spend',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column('org_budget_settings', 'litellm_last_member_spend')
    op.drop_column('org_budget_settings', 'litellm_last_team_spend')
    op.drop_column('org_budget_settings', 'litellm_last_spend_snapshot_at')
