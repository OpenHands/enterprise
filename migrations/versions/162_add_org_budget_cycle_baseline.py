"""Add per-member budget cycle baselines with provenance.

Revision ID: 162
Revises: 161
Create Date: 2026-09-11 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '162'
down_revision: str | None = '161'
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

    # Preserve malformed legacy evidence; only import finite, nonnegative numbers.
    op.execute("""
        CREATE FUNCTION pg_temp.budget_baseline_number(value json)
        RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE result double precision;
        BEGIN
            IF json_typeof(value) <> 'number' THEN RETURN NULL; END IF;
            result := (value #>> '{}')::double precision;
            IF result >= 0 AND result < 'Infinity'::double precision THEN
                RETURN result;
            END IF;
            RETURN NULL;
        EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
            RETURN NULL;
        END;
        $$;
    """)
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
            pg_temp.budget_baseline_number(baseline.value),
            'imported',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM org_budget_settings AS settings
        CROSS JOIN LATERAL json_each(CASE
            WHEN json_typeof(settings.user_cycle_start_spend) = 'object'
                THEN settings.user_cycle_start_spend
            ELSE '{}'::json END) AS baseline
        WHERE pg_temp.budget_baseline_number(baseline.value) IS NOT NULL
        """
    )
    op.execute('DROP FUNCTION pg_temp.budget_baseline_number(json)')


def downgrade() -> None:
    op.drop_table('org_budget_cycle_baseline')
