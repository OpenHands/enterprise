"""Retry credential retirement only after a committed application cutover."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text

from server.logger import logger
from storage.database import session_maker
from storage.litellm_credentials import retire_replaced_credentials
from storage.llm_credential_operation import LlmCredentialOperation
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskProcessor,
    MaintenanceTaskStatus,
)


class CredentialRetirementProcessor(MaintenanceTaskProcessor):
    org_id: UUID

    async def __call__(self, task: MaintenanceTask) -> dict:
        return await retire_replaced_credentials(self.org_id)


def enqueue_credential_retirement_tasks() -> int:
    from run_maintenance_tasks import expire_stale_tasks

    with session_maker() as session:
        if not session.scalar(
            text('SELECT pg_try_advisory_xact_lock(5712895824587010125)')
        ):
            return 0
        expire_stale_tasks(session)
        processor_type = f'{CredentialRetirementProcessor.__module__}.{CredentialRetirementProcessor.__name__}'
        queued: set[UUID] = set()
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
                    CredentialRetirementProcessor.model_validate_json(
                        task.processor_json
                    ).org_id
                )
            except ValidationError:
                logger.warning(
                    'Malformed credential retirement task', extra={'task_id': task.id}
                )
        org_ids = (
            set(
                session.scalars(
                    select(LlmCredentialOperation.org_id).where(
                        LlmCredentialOperation.replaces_key_hash.is_not(None),
                        LlmCredentialOperation.activated_at.is_not(None),
                        LlmCredentialOperation.retired_at.is_(None),
                    )
                ).all()
            )
            - queued
        )
        for org_id in sorted(org_ids):
            task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
            task.set_processor(CredentialRetirementProcessor(org_id=org_id))
            session.add(task)
        session.commit()
        return len(org_ids)
