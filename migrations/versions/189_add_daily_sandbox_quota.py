"""Replace daily conversation quota with daily sandbox quota.

The metered unit for the daily quota changes from conversations to sandbox
creations: a sandbox is what allocates infrastructure. Existing overrides are
carried over so users/orgs keep their effective limit (including -1
exemptions), and today's conversation usage rows are copied so nobody gains
a fresh allowance mid-day at rollout.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '189'
down_revision: str | None = '188'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.alter_column(
        'user',
        'daily_conversation_limit',
        new_column_name='daily_sandbox_limit',
    )
    op.alter_column(
        'org',
        'daily_conversation_limit',
        new_column_name='daily_sandbox_limit',
    )

    op.create_table(
        'daily_sandbox_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('sandbox_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.UniqueConstraint('user_id', 'usage_date'),
    )

    # Seed the new table from today's conversation usage so the rollout does
    # not reset everyone's allowance mid-day.
    op.execute(
        """
        INSERT INTO daily_sandbox_usage
            (user_id, usage_date, sandbox_count, created_at, updated_at)
        SELECT user_id, usage_date, conversation_count, created_at, updated_at
        FROM daily_conversation_usage
        WHERE usage_date = CURRENT_DATE
        ON CONFLICT (user_id, usage_date) DO NOTHING
        """
    )

    op.drop_table('daily_conversation_usage')


def downgrade() -> None:
    op.create_table(
        'daily_conversation_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('conversation_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.UniqueConstraint('user_id', 'usage_date'),
    )
    op.drop_table('daily_sandbox_usage')
    op.alter_column(
        'org',
        'daily_sandbox_limit',
        new_column_name='daily_conversation_limit',
    )
    op.alter_column(
        'user',
        'daily_sandbox_limit',
        new_column_name='daily_conversation_limit',
    )
