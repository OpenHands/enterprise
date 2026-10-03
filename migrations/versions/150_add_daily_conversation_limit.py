"""Add per-user daily conversation limits and usage accounting."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '150'
down_revision: str | None = '149'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    # NULL means "inherit the effective default at runtime" (org-level limit or
    # the OH_DAILY_CONVERSATION_LIMIT deployment default). Existing users are
    # deliberately NOT stamped with the deployment default here: a non-NULL
    # user-level value takes precedence over org-level exemptions and future
    # chart changes, so stamping would permanently pin every existing user.
    op.add_column(
        'user',
        sa.Column('daily_conversation_limit', sa.Integer(), nullable=True),
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
