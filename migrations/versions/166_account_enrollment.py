"""Add single-use account enrollment invitations.

Revision ID: 166
Revises: 165
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '166'
down_revision: str | None = '165'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'account_invitation',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('token_digest', sa.String(64), unique=True, nullable=False),
        sa.Column('reserved_account_id', sa.UUID(), nullable=False),
        sa.Column('normalized_email', sa.String(320), nullable=False),
        sa.Column('display_email', sa.String(320), nullable=False),
        sa.Column(
            'creator_account_id',
            sa.UUID(),
            sa.ForeignKey('auth_account.id'),
            nullable=False,
        ),
        sa.Column('accepted_account_id', sa.UUID(), sa.ForeignKey('auth_account.id')),
        sa.Column('org_id', sa.UUID()),
        sa.Column('org_role_id', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            '(org_id IS NULL) = (org_role_id IS NULL)',
            name='ck_account_invitation_scope',
        ),
    )
    op.create_index(
        'ix_account_invitation_normalized_email',
        'account_invitation',
        ['normalized_email'],
    )


def downgrade() -> None:
    op.drop_table('account_invitation')
