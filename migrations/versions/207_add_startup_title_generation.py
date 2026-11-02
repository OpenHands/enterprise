"""Persist title generation independently of sandbox startup.

Revision ID: 207
Revises: 206
"""

import sqlalchemy as sa
from alembic import op

revision = '207'
down_revision = '206'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('app_conversation_start_task', 'conversation_metadata'):
        op.add_column(table, sa.Column('title_generation', sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ('app_conversation_start_task', 'conversation_metadata'):
        op.drop_column(table, 'title_generation')
