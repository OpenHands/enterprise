"""Add native authentication schema without selecting an installation mode.

Revision ID: 162
Revises: 161
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '162'
down_revision: str | None = '161'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _time(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        'auth_account',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('normalized_email', sa.String(320)),
        sa.Column('display_email', sa.String(320)),
        sa.Column('state', sa.String(32), nullable=False),
        sa.Column('session_version', sa.Integer(), nullable=False),
        sa.Column('provisioning_status', sa.String(16), nullable=False),
        _time('created_at'),
        _time('updated_at'),
        sa.CheckConstraint(
            "state IN ('profile_present','reonboardable','profile_absent_blocked','deleted')",
            name='ck_auth_account_state',
        ),
    )
    op.create_check_constraint(
        'ck_auth_account_email',
        'auth_account',
        "state = 'deleted' OR (normalized_email IS NOT NULL AND display_email IS NOT NULL)",
    )
    op.create_index(
        'uq_auth_account_live_email',
        'auth_account',
        ['normalized_email'],
        unique=True,
        postgresql_where=sa.text("state <> 'deleted'"),
    )
    op.create_table(
        'password_credential',
        sa.Column(
            'account_id', sa.UUID(), sa.ForeignKey('auth_account.id'), primary_key=True
        ),
        sa.Column(
            'normalized_login_email', sa.String(320), unique=True, nullable=False
        ),
        sa.Column('display_email', sa.String(320), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('credential_version', sa.Integer(), nullable=False),
        _time('changed_at'),
    )
    op.create_table(
        'browser_session',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('token_digest', sa.String(64), unique=True, nullable=False),
        sa.Column(
            'account_id', sa.UUID(), sa.ForeignKey('auth_account.id'), nullable=False
        ),
        sa.Column('session_version', sa.Integer(), nullable=False),
        sa.Column('credential_version', sa.Integer(), nullable=False),
        sa.Column('auth_method', sa.String(32), nullable=False),
        _time('auth_time'),
        _time('created_at'),
        _time('idle_expires_at'),
        _time('absolute_expires_at'),
        _time('revoked_at', True),
    )
    op.create_index('ix_browser_session_account_id', 'browser_session', ['account_id'])
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
        _time('created_at'),
        _time('expires_at'),
        _time('consumed_at', True),
        _time('revoked_at', True),
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
    op.create_table(
        'auth_challenge',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('token_digest', sa.String(64), unique=True, nullable=False),
        sa.Column('purpose', sa.String(32), nullable=False),
        sa.Column('account_id', sa.UUID(), sa.ForeignKey('auth_account.id')),
        sa.Column('credential_version', sa.Integer()),
        sa.Column('creator_account_id', sa.UUID(), sa.ForeignKey('auth_account.id')),
        _time('created_at'),
        _time('expires_at'),
        _time('consumed_at', True),
        _time('revoked_at', True),
    )
    op.create_index('ix_auth_challenge_account_id', 'auth_challenge', ['account_id'])
    op.create_table(
        'auth_installation',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('bootstrap_account_id', sa.UUID(), sa.ForeignKey('auth_account.id')),
        _time('initialized_at'),
        _time('completed_at', True),
        sa.CheckConstraint('id = 1', name='ck_auth_installation_singleton'),
        sa.CheckConstraint(
            "mode IN ('keycloak','native')", name='ck_auth_installation_mode'
        ),
    )
    op.create_table(
        'auth_throttle',
        sa.Column('key_digest', sa.String(64), primary_key=True),
        _time('window_start'),
        sa.Column('attempts', sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        'auth_throttle',
        'auth_installation',
        'auth_challenge',
        'account_invitation',
        'browser_session',
        'password_credential',
        'auth_account',
    ):
        op.drop_table(table)
