"""Add local password authentication accounts and one-time links.

Revision ID: 175
Revises: 174
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'password_auth_account',
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('user.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('normalized_email', sa.String(320), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=True),
        sa.Column(
            'created_by_org_invitation_id',
            sa.Integer(),
            sa.ForeignKey('org_invitation.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('session_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        'ix_password_auth_account_normalized_email',
        'password_auth_account',
        ['normalized_email'],
        unique=True,
    )
    op.create_table(
        'password_auth_token',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('password_auth_account.user_id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'org_invitation_id',
            sa.Integer(),
            sa.ForeignKey('org_invitation.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column(
            'created_by_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('user.id'),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('setup', 'reset')", name='ck_password_auth_token_purpose'
        ),
    )
    op.create_index(
        'ix_password_auth_token_user_id',
        'password_auth_token',
        ['user_id'],
    )
    op.create_index(
        'ix_password_auth_token_token_digest',
        'password_auth_token',
        ['token_digest'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table('password_auth_token')
    op.drop_table('password_auth_account')
