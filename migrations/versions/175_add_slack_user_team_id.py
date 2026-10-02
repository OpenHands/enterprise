"""Associate linked Slack users with their installed workspace.

Revision ID: 175
Revises: 174
Create Date: 2026-10-01 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('slack_users', sa.Column('team_id', sa.String(), nullable=True))
    op.create_index('ix_slack_users_team_id', 'slack_users', ['team_id'], unique=False)
    op.execute(
        sa.text(
            """
            UPDATE slack_users
            SET team_id = (SELECT team_id FROM slack_teams LIMIT 1)
            WHERE team_id IS NULL
              AND (SELECT COUNT(*) FROM slack_teams) = 1
            """
        )
    )


def downgrade() -> None:
    op.drop_index('ix_slack_users_team_id', table_name='slack_users')
    op.drop_column('slack_users', 'team_id')
