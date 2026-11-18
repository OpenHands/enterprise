"""Add opt-in customer gateway org config and per-member encrypted credentials.

Additive only. Use the feature flag for operational rollback. The downgrade
retains these tables and encrypted keys; re-upgrade is idempotent.

Revision ID: 219
Revises: 218
Create Date: 2026-11-14
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '219'
down_revision: str | None = '218'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: downgrade retains the tables (and encrypted member keys), so a
    # re-upgrade after a rollback finds them in place.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'customer_gateway_org_config' in existing:
        return
    op.create_table(
        'customer_gateway_org_config',
        sa.Column(
            'org_id',
            sa.Uuid(),
            sa.ForeignKey('org.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('gateway_id', sa.String(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('profile', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.String(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('enabled_since', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'customer_gateway_member_credential',
        sa.Column('org_id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), primary_key=True),
        sa.Column('ciphertext', sa.String(), nullable=False),
        sa.Column('fingerprint', sa.String(16), nullable=False),
        sa.Column(
            'credential_version', sa.Integer(), nullable=False, server_default='1'
        ),
        sa.Column('policy_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('allowed_models', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('updated_by', sa.String(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ['org_id', 'user_id'],
            ['org_member.org_id', 'org_member.user_id'],
            ondelete='CASCADE',
        ),
    )


def downgrade() -> None:
    # Non-destructive: rollback is switch-off only. The tables and encrypted member keys
    # are retained; older code never reads them. Drop them manually only on purpose.
    pass
