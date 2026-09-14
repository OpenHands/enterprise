"""Persist budget ownership and retryable write intent.

Revision ID: 163
Revises: 162
Create Date: 2026-09-14
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.exceptions import BudgetOwnershipDowngradeError

revision: str = '163'
down_revision: str | None = '162'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, column_type in (
        ('cycle_end_at', sa.DateTime(timezone=True)),
        ('cycle_allowance', sa.Float()),
        ('cycle_default_user_allowance', sa.Float()),
        ('cycle_user_allowances', sa.JSON()),
    ):
        op.add_column(
            'org_budget_settings', sa.Column(name, column_type, nullable=True)
        )
    op.add_column(
        'org_budget_settings',
        sa.Column(
            'control_mode', sa.String(32), nullable=False, server_default='external'
        ),
    )
    op.add_column(
        'org_budget_settings',
        sa.Column(
            'control_generation', sa.Integer(), nullable=False, server_default='0'
        ),
    )
    op.add_column(
        'org_budget_settings',
        sa.Column('control_changed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'org_budget_settings',
        sa.Column('control_changed_by', sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE org_budget_settings SET control_mode = 'needs_adoption' WHERE enabled"
    )
    op.create_check_constraint(
        'ck_org_budget_control_mode',
        'org_budget_settings',
        "control_mode IN ('managed', 'external', 'needs_adoption')",
    )
    op.create_check_constraint(
        'ck_org_budget_generation', 'org_budget_settings', 'control_generation >= 0'
    )
    op.create_table(
        'org_budget_operation',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('actor', sa.String(), nullable=False),
        sa.Column('plan', postgresql.JSONB(), nullable=False),
        sa.Column('verification', postgresql.JSONB(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('last_error', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.UniqueConstraint(
            'org_id', 'idempotency_key', name='uq_budget_operation_key'
        ),
        sa.UniqueConstraint(
            'org_id', 'generation', name='uq_budget_operation_generation'
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'abandoned')",
            name='ck_budget_operation_status',
        ),
        sa.CheckConstraint(
            "kind IN ('adopt', 'settings', 'rollover', 'repair')",
            name='ck_budget_operation_kind',
        ),
        sa.CheckConstraint('generation > 0', name='ck_budget_operation_generation'),
    )
    op.create_index(
        'uq_budget_operation_pending',
        'org_budget_operation',
        ['org_id'],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute("""
        CREATE FUNCTION protect_budget_operation_intent() RETURNS trigger AS $$
        BEGIN
            IF (NEW.id, NEW.org_id, NEW.idempotency_key, NEW.request_hash,
                NEW.generation, NEW.kind, NEW.actor, NEW.plan, NEW.created_at)
                IS DISTINCT FROM
               (OLD.id, OLD.org_id, OLD.idempotency_key, OLD.request_hash,
                OLD.generation, OLD.kind, OLD.actor, OLD.plan, OLD.created_at)
            THEN
                RAISE EXCEPTION 'Budget operation intent is immutable';
            END IF;
            IF OLD.status <> 'pending' AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'A finished budget operation cannot be reopened';
            END IF;
            IF OLD.status <> 'pending'
               AND NEW.verification IS DISTINCT FROM OLD.verification THEN
                RAISE EXCEPTION 'Budget verification evidence is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER protect_budget_operation_intent
        BEFORE UPDATE ON org_budget_operation
        FOR EACH ROW EXECUTE FUNCTION protect_budget_operation_intent();
    """)


def downgrade() -> None:
    raise BudgetOwnershipDowngradeError(
        'Budget ownership cannot be safely removed by an online downgrade. '
        'Use an ownership-aware rollback release; a pre-163 restore requires '
        'quiescing all OpenHands writers and a coordinated database restore.'
    )
