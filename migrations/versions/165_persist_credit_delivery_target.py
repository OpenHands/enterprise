"""Persist a payment's native target before delivering its credit.

Revision ID: 165
Revises: 164
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

from migrations.exceptions import BudgetOwnershipDowngradeError

revision = '165'
down_revision = '164'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('billing_sessions', sa.Column('credit_target', sa.Float()))
    op.add_column('billing_sessions', sa.Column('credit_budget_before', sa.Float()))
    op.create_check_constraint(
        'ck_billing_credit_target',
        'billing_sessions',
        "credit_target IS NULL OR (credit_target >= 0 AND credit_target < 'Infinity'::float AND org_id IS NOT NULL AND status IN ('in_progress', 'completed'))",
    )
    op.create_index(
        'uq_pending_credit_org',
        'billing_sessions',
        ['org_id'],
        unique=True,
        postgresql_where=sa.text(
            "status = 'in_progress' AND credit_target IS NOT NULL"
        ),
    )
    op.execute("""
        CREATE FUNCTION protect_billing_credit_target() RETURNS trigger AS $$
        BEGIN
            IF OLD.credit_target IS NOT NULL AND (
                (NEW.id, NEW.org_id, NEW.user_id, NEW.price, NEW.credit_target,
                 NEW.credit_budget_before) IS DISTINCT FROM
                (OLD.id, OLD.org_id, OLD.user_id, OLD.price, OLD.credit_target,
                 OLD.credit_budget_before)
                OR NEW.status NOT IN ('in_progress', 'completed')
                OR (OLD.status = 'completed' AND NEW.status <> 'completed')
            ) THEN
                RAISE EXCEPTION 'Credit delivery intent is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER protect_billing_credit_target
        BEFORE UPDATE ON billing_sessions
        FOR EACH ROW EXECUTE FUNCTION protect_billing_credit_target();
    """)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            'SELECT EXISTS (SELECT 1 FROM billing_sessions WHERE credit_target IS NOT NULL)'
        )
    ):
        raise BudgetOwnershipDowngradeError(
            'Credit delivery receipts must be preserved'
        )
    op.execute('DROP TRIGGER protect_billing_credit_target ON billing_sessions')
    op.execute('DROP FUNCTION protect_billing_credit_target()')
    op.drop_index('uq_pending_credit_org', table_name='billing_sessions')
    op.drop_constraint('ck_billing_credit_target', 'billing_sessions', type_='check')
    op.drop_column('billing_sessions', 'credit_budget_before')
    op.drop_column('billing_sessions', 'credit_target')
