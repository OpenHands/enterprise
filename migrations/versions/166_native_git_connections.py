"""Independent Git connections and session-bound OAuth state.

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
        'native_git_connection',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column(
            'account_id', sa.UUID(), sa.ForeignKey('auth_account.id'), nullable=False
        ),
        sa.Column('provider', sa.String(32), nullable=False),
        sa.Column('host', sa.String(255), nullable=False),
        sa.Column('subject', sa.String(255)),
        sa.Column('login', sa.String(255)),
        sa.Column('display_name', sa.String(255)),
        sa.Column('avatar_url', sa.Text()),
        sa.Column('auth_type', sa.String(16), nullable=False),
        sa.Column('encrypted_access_token', sa.Text()),
        sa.Column('encrypted_refresh_token', sa.Text()),
        sa.Column('encrypted_email', sa.Text()),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('refresh_expires_at', sa.DateTime(timezone=True)),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('identity_verified', sa.Boolean(), nullable=False),
        sa.Column('last_error', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            'account_id', 'provider', name='uq_native_git_account_provider'
        ),
    )
    op.create_index(
        'ix_native_git_connection_account_id', 'native_git_connection', ['account_id']
    )
    op.create_index(
        'uq_native_git_subject',
        'native_git_connection',
        ['provider', 'host', 'subject'],
        unique=True,
        postgresql_where=sa.text('revoked_at IS NULL AND subject IS NOT NULL'),
    )
    op.create_table(
        'native_git_oauth_state',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('token_digest', sa.String(64), nullable=False, unique=True),
        sa.Column(
            'account_id', sa.UUID(), sa.ForeignKey('auth_account.id'), nullable=False
        ),
        sa.Column(
            'session_id',
            sa.UUID(),
            sa.ForeignKey('browser_session.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('provider', sa.String(32), nullable=False),
        sa.Column('host', sa.String(255), nullable=False),
        sa.Column('connection_generation', sa.Integer(), nullable=False),
        sa.Column('encrypted_verifier', sa.Text()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
    )
    op.create_index(
        'ix_native_git_oauth_state_account_id', 'native_git_oauth_state', ['account_id']
    )


def downgrade() -> None:
    op.drop_table('native_git_oauth_state')
    op.drop_table('native_git_connection')
