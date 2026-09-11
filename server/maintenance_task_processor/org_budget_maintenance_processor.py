from __future__ import annotations

from uuid import UUID

from server.logger import logger
from server.services.org_budget_service import OrgBudgetService
from storage.database import a_session_maker
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskProcessor


class OrgBudgetMaintenanceProcessor(MaintenanceTaskProcessor):
    org_ids: list[str]

    async def __call__(self, task: MaintenanceTask) -> dict:
        processed = 0
        failed = 0
        errors: list[dict[str, str]] = []

        async with a_session_maker() as session:
            service = OrgBudgetService(db_session=session)
            for org_id in self.org_ids:
                try:
                    org_uuid = UUID(org_id)
                except ValueError:
                    failed += 1
                    errors.append({'org_id': org_id, 'error': 'invalid_uuid'})
                    continue

                try:
                    result = await service.run_budget_maintenance(org_uuid)
                    # Commit per-org so healthy orgs persist independently and so
                    # failed orgs keep their diagnostic litellm_last_sync_status
                    # recording. External LiteLLM writes already happened; the
                    # settings reflect "reconciliation attempted".
                    await session.commit()
                    status = result.get('status', 'success')
                    if status == 'error':
                        failed += 1
                        errors.append(
                            {
                                'org_id': org_id,
                                'error': result.get('reason') or 'sync_failed',
                                'drift': result.get('drift', []),
                            }
                        )
                        continue
                    processed += 1
                except Exception as exc:
                    await session.rollback()
                    logger.exception(
                        'org_budget_maintenance_failed',
                        extra={
                            'org_id': org_id,
                        },
                        stack_info=True,
                    )
                    failed += 1
                    errors.append({'org_id': org_id, 'error': str(exc)})

        return {
            'processed': processed,
            'failed': failed,
            'error_count': len(errors),
            'errors': errors[:20],
            'success': failed == 0,
        }
