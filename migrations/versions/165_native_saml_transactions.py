"""Persist browser-bound SAML requests and cross-worker replay protection.

Revision ID: 165
Revises: 164
"""

import sqlalchemy as sa
from alembic import op

revision = '165'
down_revision = '164'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'saml_transaction',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('relay_digest', sa.String(64), nullable=False, unique=True),
        sa.Column('browser_digest', sa.String(64), nullable=False, unique=True),
        sa.Column('request_id', sa.String(128), nullable=False, unique=True),
        sa.Column('connection_id', sa.String(64), nullable=False),
        sa.Column('configuration_digest', sa.String(64), nullable=False),
        sa.Column('return_path', sa.String(2048), nullable=False),
        sa.Column('context', sa.String(), nullable=False),
        sa.Column('verified_claims', sa.String()),
        sa.Column('link_account_id', sa.Uuid(), sa.ForeignKey('auth_account.id')),
        sa.Column('link_session_id', sa.Uuid(), sa.ForeignKey('browser_session.id')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
    )
    op.create_index(
        'ix_saml_transaction_expires_at', 'saml_transaction', ['expires_at']
    )
    op.create_table(
        'saml_replay',
        sa.Column('id_digest', sa.String(64), primary_key=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_saml_replay_expires_at', 'saml_replay', ['expires_at'])


def downgrade() -> None:
    op.drop_table('saml_replay')
    op.drop_table('saml_transaction')
