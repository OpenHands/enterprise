"""Payment authority uses real PostgreSQL receipts and the shared budget lock."""

from importlib import import_module
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from migrations.exceptions import BudgetOwnershipDowngradeError
from storage.billing_session import BillingSession
from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    budget_control_session,
)
from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_settings import OrgBudgetSettings


def receipt(org_id, **kwargs):
    return (
        dict(
            id='checkout',
            org_id=org_id,
            user_id='member',
            price=25,
            price_code='NA',
            credit_target=125,
            credit_budget_before=100,
        )
        | kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('write_style', ['orm', 'bulk', 'sql'])
async def test_credit_authority_requires_committed_receipt(
    async_engine, create_org, write_style
):
    org = create_org()
    async with budget_control_session(async_engine, org.id) as control:
        if write_style == 'orm':
            control.session.add(BillingSession(**receipt(org.id)))
            await control.session.flush()
        elif write_style == 'bulk':
            await control.session.execute(
                insert(BillingSession).values(**receipt(org.id))
            )
        else:
            await control.session.execute(
                text("""
                INSERT INTO billing_sessions
                    (id, org_id, user_id, price, price_code, credit_target, credit_budget_before, status, created_at, updated_at)
                VALUES ('checkout', :org_id, 'member', 25, 'NA', 125, 100, 'in_progress', now(), now())
            """),
                {'org_id': org.id},
            )
        with pytest.raises(BudgetWriteDenied, match='not durable'):
            await control.authorize_credit_target('checkout', 125)
        await control.session.commit()
        await control.authorize_credit_target('checkout', 125)
        for session_id, target in [('other', 125), ('checkout', 150)]:
            with pytest.raises(BudgetWriteDenied, match='matching durable target'):
                await control.authorize_credit_target(session_id, target)


@pytest.mark.asyncio
async def test_credit_writer_refuses_missing_lock_before_http(create_org):
    org = create_org()
    with patch('storage.lite_llm_manager.httpx.AsyncClient') as client:
        with pytest.raises(BudgetWriteDenied, match='shared organization budget lock'):
            await LiteLlmManager.update_team_and_users_budget(
                str(org.id), 125, billing_session_id='checkout'
            )
    client.assert_not_called()


@pytest.mark.asyncio
async def test_pending_payment_excludes_adoption_and_handoff(async_engine, create_org):
    org = create_org()
    async with budget_control_session(async_engine, org.id) as control:
        control.session.add_all(
            [
                BillingSession(**receipt(org.id)),
                OrgBudgetSettings(org_id=org.id, control_mode='needs_adoption'),
            ]
        )
        await control.session.commit()
        with pytest.raises(BudgetControlConflict, match='pending credit delivery'):
            await control.reserve_operation(
                idempotency_key='adopt',
                request_hash='x',
                kind='adopt',
                actor='admin',
                plan={},
            )
        with pytest.raises(BudgetControlConflict, match='pending credit delivery'):
            await control.hand_off('admin')


@pytest.mark.asyncio
async def test_pending_budget_operation_excludes_payment(async_engine, create_org):
    org = create_org()
    async with budget_control_session(async_engine, org.id) as control:
        control.session.add(
            OrgBudgetSettings(org_id=org.id, control_mode='needs_adoption')
        )
        await control.session.commit()
        operation = await control.reserve_operation(
            idempotency_key='adopt',
            request_hash='x',
            kind='adopt',
            actor='admin',
            plan={},
        )
        control.session.add(BillingSession(**receipt(org.id)))
        await control.session.commit()
        with pytest.raises(BudgetControlConflict, match='pending budget operation'):
            await control.authorize_credit_target('checkout', 125)
        with pytest.raises(BudgetControlConflict, match='pending credit delivery'):
            await control.require_executable(operation)


@pytest.mark.parametrize(
    'assignment',
    [
        'credit_target = 150',
        'credit_target = NULL',
        'credit_budget_before = 0',
        'price = 50',
        "status = 'cancelled'",
        "user_id = 'other'",
        'org_id = NULL',
    ],
)
def test_sql_cannot_rewrite_credit_receipt(engine, create_org, assignment):
    org = create_org()
    with engine.begin() as connection:
        connection.execute(insert(BillingSession).values(**receipt(org.id)))
    with pytest.raises(DBAPIError, match='Credit delivery intent is immutable'):
        with engine.begin() as connection:
            connection.execute(text(f'UPDATE billing_sessions SET {assignment}'))


def test_one_pending_payment_per_org_and_completed_receipt_is_final(engine, create_org):
    org = create_org()
    with engine.begin() as connection:
        connection.execute(insert(BillingSession).values(**receipt(org.id)))
    with pytest.raises(IntegrityError, match='uq_pending_credit_org'):
        with engine.begin() as connection:
            connection.execute(
                insert(BillingSession).values(**receipt(org.id, id='second'))
            )
    with engine.begin() as connection:
        connection.execute(text("UPDATE billing_sessions SET status = 'completed'"))
        connection.execute(
            insert(BillingSession).values(**receipt(org.id, id='second'))
        )
    with pytest.raises(DBAPIError, match='Credit delivery intent is immutable'):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE billing_sessions SET status = 'in_progress' WHERE id = 'checkout'"
                )
            )


@pytest.mark.parametrize('target', [float('nan'), float('inf'), float('-inf'), -1])
def test_invalid_credit_target_is_rejected(engine, create_org, target):
    org = create_org()
    with pytest.raises(DBAPIError, match='ck_billing_credit_target'):
        with engine.begin() as connection:
            connection.execute(
                insert(BillingSession).values(**receipt(org.id, credit_target=target))
            )


def test_downgrade_preserves_credit_receipts(engine, create_org):
    org = create_org()
    migration = import_module('migrations.versions.165_persist_credit_delivery_target')
    with engine.begin() as connection:
        connection.execute(insert(BillingSession).values(**receipt(org.id)))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            with pytest.raises(
                BudgetOwnershipDowngradeError, match='receipts must be preserved'
            ):
                migration.downgrade()
        assert (
            connection.scalar(text('SELECT credit_target FROM billing_sessions')) == 125
        )
