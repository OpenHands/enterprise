"""Resume only payments whose native delivery target is already committed."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text

from server.logger import logger
from server.services.credit_delivery_service import deliver_checkout_credit
from storage.billing_session import BillingSession
from storage.budget_control import (
    BudgetWriteDenied,
    budget_control_session,
    budget_engine,
)
from storage.database import a_session_maker, session_maker
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskProcessor,
    MaintenanceTaskStatus,
)


class CreditDeliveryProcessor(MaintenanceTaskProcessor):
    org_id: UUID
    checkout_session_id: str

    async def __call__(self, task: MaintenanceTask) -> dict:
        async with a_session_maker() as session:
            engine = budget_engine(session)
        async with budget_control_session(engine, self.org_id) as control:
            receipt = await control.session.get(
                BillingSession, self.checkout_session_id
            )
            if (
                receipt is None
                or receipt.org_id != self.org_id
                or receipt.credit_target is None
            ):
                raise BudgetWriteDenied('No durable credit delivery receipt')
            await deliver_checkout_credit(control, self.checkout_session_id)
        return {'completed': True}


def enqueue_credit_delivery_tasks() -> int:
    from run_maintenance_tasks import expire_stale_tasks

    with session_maker() as session:
        if not session.scalar(
            text('SELECT pg_try_advisory_xact_lock(5712895824587010126)')
        ):
            return 0
        expire_stale_tasks(session)
        processor_type = (
            f'{CreditDeliveryProcessor.__module__}.{CreditDeliveryProcessor.__name__}'
        )
        queued: set[str] = set()
        for task in session.scalars(
            select(MaintenanceTask).where(
                MaintenanceTask.processor_type == processor_type,
                MaintenanceTask.status.in_(
                    [MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING]
                ),
            )
        ):
            try:
                queued.add(
                    CreditDeliveryProcessor.model_validate_json(
                        task.processor_json
                    ).checkout_session_id
                )
            except ValidationError:
                logger.warning(
                    'Malformed credit delivery task', extra={'task_id': task.id}
                )
        count = 0
        for receipt in session.scalars(
            select(BillingSession)
            .where(
                BillingSession.status == 'in_progress',
                BillingSession.credit_target.is_not(None),
                BillingSession.org_id.is_not(None),
            )
            .order_by(BillingSession.created_at, BillingSession.id)
        ):
            if receipt.id in queued:
                continue
            task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
            task.set_processor(
                CreditDeliveryProcessor(
                    org_id=receipt.org_id, checkout_session_id=receipt.id
                )
            )
            session.add(task)
            count += 1
        session.commit()
        return count
