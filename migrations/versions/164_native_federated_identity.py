"""Add trusted external identities to native accounts.

Revision ID: 164
Revises: 163
"""

import sqlalchemy as sa
from alembic import op

revision = '164'
down_revision = '163'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'external_identity',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column(
            'account_id', sa.Uuid(), sa.ForeignKey('auth_account.id'), nullable=False
        ),
        sa.Column('connection_id', sa.String(128), nullable=False),
        sa.Column('issuer', sa.String(512), nullable=False),
        sa.Column('subject', sa.String(512), nullable=False),
        sa.Column('auth_method', sa.String(32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            'auth_method',
            'connection_id',
            'issuer',
            'subject',
            name='uq_external_identity_subject',
        ),
    )
    op.create_index(
        'ix_external_identity_account_id', 'external_identity', ['account_id']
    )
    op.add_column('browser_session', sa.Column('external_identity_id', sa.Uuid()))
    op.create_foreign_key(
        'fk_browser_session_external_identity',
        'browser_session',
        'external_identity',
        ['external_identity_id'],
        ['id'],
    )


def downgrade() -> None:
    # An old server requires passwords for every live account. Refuse a lossy
    # downgrade once federation has been used, rather than strand those accounts
    # or erase the stable subject ownership needed to prevent account takeover.
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM external_identity)')):
        raise RuntimeError('Cannot downgrade while native external identities exist')
    op.drop_constraint(
        'fk_browser_session_external_identity', 'browser_session', type_='foreignkey'
    )
    op.drop_column('browser_session', 'external_identity_id')
    op.drop_table('external_identity')
