"""Add quota increase request table and work email columns."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '152'
down_revision: str | None = '151'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.add_column('user', sa.Column('work_email', sa.String(255), nullable=True))
    op.add_column(
        'user',
        sa.Column('work_email_verified_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'quota_increase_request',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('work_email', sa.String(255), nullable=False),
        sa.Column('baseline_limit', sa.Integer(), nullable=False),
        sa.Column('requested_limit', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column(
            'status',
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by_user_id', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['user.id']),
    )

    op.create_index(
        'ix_quota_increase_request_user_id',
        'quota_increase_request',
        ['user_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_quota_increase_request_user_id', table_name='quota_increase_request'
    )
    op.drop_table('quota_increase_request')
    op.drop_column('user', 'work_email_verified_at')
    op.drop_column('user', 'work_email')
