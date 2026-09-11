"""Add per-member budget cycle baselines with provenance.

Revision ID: 161
Revises: 160
Create Date: 2026-09-11 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '161'
down_revision: str | None = '160'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.create_table(
        'org_budget_cycle_baseline',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('cycle_start_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('baseline_spend', sa.Float(), nullable=False),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recovery_generation', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.UniqueConstraint(
            'org_id',
            'cycle_start_at',
            'user_id',
            name='uq_org_budget_cycle_baseline_member_cycle',
        ),
    )

    # Import the JSON map as the current cycle's baselines so every existing
    # cap references a row from the first post-upgrade load onwards. The map
    # holds one float per member and the settings row is unique per org, so
    # the rows cannot collide.
    op.execute(
        """
        INSERT INTO org_budget_cycle_baseline (
            org_id,
            user_id,
            cycle_start_at,
            baseline_spend,
            source,
            observed_at,
            created_at,
            updated_at
        )
        SELECT
            settings.org_id,
            baseline.key,
            settings.cycle_start_at,
            baseline.value::double precision,
            'imported',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM org_budget_settings AS settings
        CROSS JOIN LATERAL json_each_text(settings.user_cycle_start_spend) AS baseline
        WHERE baseline.value IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table('org_budget_cycle_baseline')
