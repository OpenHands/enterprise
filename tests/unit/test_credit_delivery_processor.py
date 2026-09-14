"""Real database discovery, deduplication and stale-worker recovery for credits."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from server.maintenance_task_processor.credit_delivery_processor import (
    CreditDeliveryProcessor,
    enqueue_credit_delivery_tasks,
)
from storage.billing_session import BillingSession
from storage.budget_control import BudgetWriteDenied
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus


@pytest.fixture
def pending_payment(session_maker, create_org):
    org = create_org()
    with session_maker() as session:
        session.add(
            BillingSession(
                id='pending-credit',
                org_id=org.id,
                user_id=str(uuid4()),
                price=25,
                price_code='NA',
                credit_target=125,
            )
        )
        session.commit()
    return org.id


@pytest.fixture
def isolated_database(session_maker, async_session_maker):
    with (
        patch(
            'server.maintenance_task_processor.credit_delivery_processor.session_maker',
            session_maker,
        ),
        patch(
            'server.maintenance_task_processor.credit_delivery_processor.a_session_maker',
            async_session_maker,
        ),
    ):
        yield


def test_credit_discovery_deduplicates_and_requeues_stale_workers(
    isolated_database,
    pending_payment,
    session_maker,
):
    assert enqueue_credit_delivery_tasks() == 1
    assert enqueue_credit_delivery_tasks() == 0
    with session_maker() as session:
        task = session.scalars(select(MaintenanceTask)).one()
        processor = task.get_processor()
        assert isinstance(processor, CreditDeliveryProcessor)
        assert processor.org_id == pending_payment
        assert processor.checkout_session_id == 'pending-credit'
        task.status = MaintenanceTaskStatus.WORKING
        task.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        session.commit()
        old_id = task.id
    assert enqueue_credit_delivery_tasks() == 1
    assert enqueue_credit_delivery_tasks() == 0
    with session_maker() as session:
        assert (
            session.get(MaintenanceTask, old_id).status == MaintenanceTaskStatus.ERROR
        )
        tasks = session.scalars(
            select(MaintenanceTask).where(
                MaintenanceTask.status == MaintenanceTaskStatus.PENDING
            )
        ).all()
        assert len(tasks) == 1


def test_credit_discovery_skips_checkouts_without_durable_targets(
    isolated_database,
    session_maker,
    create_org,
):
    org = create_org()
    with session_maker() as session:
        session.add_all(
            [
                BillingSession(
                    id=state,
                    org_id=org.id,
                    user_id=str(uuid4()),
                    price=25,
                    price_code='NA',
                    status=state,
                )
                for state in ('in_progress', 'completed', 'cancelled')
            ]
        )
        session.commit()
    assert enqueue_credit_delivery_tasks() == 0


def test_credit_discovery_obeys_cross_process_lock(
    isolated_database,
    pending_payment,
    session_maker,
):
    with session_maker() as other:
        assert other.scalar(
            text('SELECT pg_try_advisory_xact_lock(5712895824587010126)')
        )
        assert enqueue_credit_delivery_tasks() == 0
        other.rollback()
    assert enqueue_credit_delivery_tasks() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt_kind', ['missing', 'unreserved', 'wrong_org'])
async def test_credit_worker_cannot_initiate_or_redirect_delivery(
    isolated_database,
    session_maker,
    create_org,
    receipt_kind,
):
    org = create_org()
    with session_maker() as session:
        if receipt_kind != 'missing':
            session.add(
                BillingSession(
                    id='checkout',
                    org_id=org.id,
                    user_id=str(uuid4()),
                    price=25,
                    price_code='NA',
                    credit_target=125 if receipt_kind == 'wrong_org' else None,
                )
            )
            session.commit()
    processor = CreditDeliveryProcessor(
        org_id=create_org().id if receipt_kind == 'wrong_org' else org.id,
        checkout_session_id='checkout',
    )
    with patch(
        'server.maintenance_task_processor.credit_delivery_processor.deliver_checkout_credit'
    ) as deliver:
        with pytest.raises(
            BudgetWriteDenied, match='No durable credit delivery receipt'
        ):
            await processor(MaintenanceTask())
        deliver.assert_not_called()
