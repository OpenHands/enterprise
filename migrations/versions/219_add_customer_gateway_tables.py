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


_ORG_CONFIG = 'customer_gateway_org_config'
_MEMBER_CREDENTIAL = 'customer_gateway_member_credential'


def upgrade() -> None:
    # Idempotent: downgrade retains the tables (and encrypted member keys), so a
    # re-upgrade after a rollback finds them in place. Checked and created
    # independently per table (not "if the first one exists, assume both do")
    # so a partial prior run -- e.g. this migration created
    # customer_gateway_org_config and then failed/was interrupted before
    # customer_gateway_member_credential -- is recovered from by creating
    # just the missing table, rather than either being silently skipped
    # (if only the first table's existence were checked) or crashing with a
    # raw "relation already exists" error from a blind create_table. Each
    # table only has FKs to the already-existing org/org_member tables, not
    # to each other, so creating them independently and in either order is
    # safe.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if _ORG_CONFIG not in existing:
        op.create_table(
            _ORG_CONFIG,
            sa.Column(
                'org_id',
                sa.Uuid(),
                sa.ForeignKey('org.id', ondelete='CASCADE'),
                primary_key=True,
            ),
            sa.Column('gateway_id', sa.String(), nullable=False),
            sa.Column(
                'enabled', sa.Boolean(), nullable=False, server_default=sa.false()
            ),
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
    if _MEMBER_CREDENTIAL not in existing:
        op.create_table(
            _MEMBER_CREDENTIAL,
            sa.Column('org_id', sa.Uuid(), primary_key=True),
            sa.Column('user_id', sa.Uuid(), primary_key=True),
            sa.Column('ciphertext', sa.String(), nullable=False),
            sa.Column('fingerprint', sa.String(16), nullable=False),
            sa.Column(
                'credential_version', sa.Integer(), nullable=False, server_default='1'
            ),
            sa.Column(
                'policy_version', sa.Integer(), nullable=False, server_default='1'
            ),
            sa.Column('allowed_models', sa.JSON(), nullable=False),
            sa.Column(
                'enabled', sa.Boolean(), nullable=False, server_default=sa.true()
            ),
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
