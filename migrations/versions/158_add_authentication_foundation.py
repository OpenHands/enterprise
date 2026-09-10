"""Add local credentials, identity links, sessions, and installation auth state.

Revision ID: 158
Revises: 157
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '158'
down_revision: str | None = '157'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'local_credentials',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('normalized_email', sa.String(320), nullable=False),
        sa.Column('password_hash', sa.String(512), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('user_id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('normalized_email'),
        sa.CheckConstraint(
            'normalized_email = lower(trim(normalized_email)) '
            'AND length(normalized_email) > 0',
            name='ck_local_credentials_normalized_email',
        ),
    )
    op.create_table(
        'external_identities',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection', sa.String(255), nullable=False),
        sa.Column('issuer', sa.String(2048), nullable=False),
        sa.Column('subject', sa.String(512), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.UniqueConstraint(
            'connection', 'issuer', 'subject', name='uq_external_identity'
        ),
    )
    op.create_index(
        'ix_external_identities_user_id', 'external_identities', ['user_id']
    )
    op.create_table(
        'auth_sessions',
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('restricted', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('token_digest'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.CheckConstraint('length(token_digest) = 64', name='ck_auth_session_digest'),
        sa.CheckConstraint('expires_at > created_at', name='ck_auth_session_expiry'),
    )
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'])
    op.create_index('ix_auth_sessions_expires_at', 'auth_sessions', ['expires_at'])
    op.create_table(
        'auth_action_tokens',
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column('purpose', sa.String(32), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(320), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('token_digest'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.CheckConstraint('length(token_digest) = 64', name='ck_auth_action_digest'),
        sa.CheckConstraint('expires_at > created_at', name='ck_auth_action_expiry'),
        sa.CheckConstraint(
            "purpose IN ('password_reset', 'email_verification', 'invitation')",
            name='ck_auth_action_purpose',
        ),
    )
    op.create_index('ix_auth_action_tokens_user_id', 'auth_action_tokens', ['user_id'])
    op.create_index(
        'ix_auth_action_tokens_expires_at', 'auth_action_tokens', ['expires_at']
    )
    op.create_table(
        'installation_auth',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('bootstrap_admin_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('bootstrap_complete', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('id = 1', name='ck_installation_auth_singleton'),
        sa.CheckConstraint(
            "mode IN ('local', 'keycloak')", name='ck_installation_auth_mode'
        ),
        sa.CheckConstraint(
            'NOT bootstrap_complete OR bootstrap_admin_id IS NOT NULL',
            name='ck_installation_auth_bootstrap',
        ),
    )


def downgrade() -> None:
    op.drop_table('installation_auth')
    op.drop_table('auth_action_tokens')
    op.drop_table('auth_sessions')
    op.drop_table('external_identities')
    op.drop_table('local_credentials')
