"""Add per-user daily conversation limits and usage accounting."""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '148'
down_revision: Union[str, None] = '147'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.add_column(
        'user',
        sa.Column('daily_conversation_limit', sa.Integer(), nullable=True),
    )

    # Persist the deployed default for existing users during the first rollout.
    # Keeping NULL when the enterprise default is unset preserves unlimited mode
    # and lets future chart changes take effect for users without an override.
    raw_default = os.getenv('OH_DAILY_CONVERSATION_LIMIT', '').strip()
    if raw_default:
        op.execute(
            sa.text(
                'UPDATE "user" SET daily_conversation_limit = :limit '
                'WHERE daily_conversation_limit IS NULL'
            ),
            {'limit': int(raw_default)},
        )

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


def downgrade() -> None:
    op.drop_table('daily_conversation_usage')
    op.drop_column('user', 'daily_conversation_limit')
