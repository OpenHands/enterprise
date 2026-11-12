"""Add canonical product settings fields.

Revision ID: 175
Revises: 174
Create Date: 2026-06-16 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ('user', 'user_settings')


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column('enabled_skills', postgresql.JSON(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                'run_router_at_conversation_start',
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                'workspace_file_discovery',
                postgresql.JSON(),
                server_default=sa.text("'{}'::json"),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                'default_profile_migrated',
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, 'default_profile_migrated')
        op.drop_column(table, 'workspace_file_discovery')
        op.drop_column(table, 'run_router_at_conversation_start')
        op.drop_column(table, 'enabled_skills')
