"""Durable native external provisioning and cleanup claims.

Revision ID: 163
Revises: 162
"""

import sqlalchemy as sa
from alembic import op

revision = '163'
down_revision = '162'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'native_external_work',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('account_id', sa.Uuid(), sa.ForeignKey('auth_account.id')),
        sa.Column('org_id', sa.Uuid()),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('payload', sa.String(), nullable=False),
        sa.Column('claim_id', sa.Uuid()),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    for name in ('account_id', 'org_id', 'status'):
        op.create_index(
            f'ix_native_external_work_{name}', 'native_external_work', [name]
        )
    op.add_column('org_member', sa.Column('native_provisioning_id', sa.Uuid()))


def downgrade() -> None:
    op.drop_column('org_member', 'native_provisioning_id')
    op.drop_table('native_external_work')
