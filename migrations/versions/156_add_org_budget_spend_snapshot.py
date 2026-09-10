"""Add last-known-good LiteLLM budget spend snapshot.

Revision ID: 156
Revises: 155
Create Date: 2026-08-25 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '156'
down_revision: str | None = '155'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    op.add_column(
        'org_budget_settings',
        sa.Column(
            'litellm_known_member_ids',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.execute(
        """
        UPDATE org_budget_settings AS settings
        SET litellm_known_member_ids = COALESCE(
            (
                SELECT json_agg(member.user_id::text ORDER BY member.user_id::text)
                FROM org_member AS member
                WHERE member.org_id = settings.org_id
            ),
            '[]'::json
        )
        """
    )


def downgrade() -> None:
    op.drop_column('org_budget_settings', 'litellm_known_member_ids')
    op.drop_column('org_budget_settings', 'litellm_last_member_spend')
    op.drop_column('org_budget_settings', 'litellm_last_team_spend')
    op.drop_column('org_budget_settings', 'litellm_last_spend_snapshot_at')
