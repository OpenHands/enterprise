from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from openhands.sdk.settings import apply_agent_settings_diff
from server.logger import logger
from storage.budget_control import (
    BudgetWriteDenied,
    budget_control_session,
    budget_engine,
)
from storage.database import a_session_maker, session_maker
from storage.lite_llm_manager import LiteLlmManager, get_openhands_cloud_key_alias
from storage.maintenance_task import (
    MaintenanceTask,
    MaintenanceTaskProcessor,
    MaintenanceTaskStatus,
)
from storage.org import Org
from storage.org_member import (
    MANAGED_LLM_KEY_OWNERSHIP_VERSION,
    OrgMember,
)
from storage.org_store import OrgStore
from storage.saas_settings_store import managed_llm_key_config_from_model

MANAGED_KEY_REPAIR_BATCH_SIZE = 25


class ManagedLlmKeyOwnershipTarget(BaseModel):
    org_id: str
    user_id: str


class ManagedLlmKeyOwnershipProcessor(MaintenanceTaskProcessor):
    """Verify and repair legacy managed keys without touching shared owners."""

    targets: list[ManagedLlmKeyOwnershipTarget]

    @staticmethod
    def _effective_managed_key_config(org: Org, member: OrgMember):
        org_settings = OrgStore.get_agent_settings_from_org(org)
        member_diff = dict(member.agent_settings_diff or {})
        member_diff.pop('mcp_config', None)
        effective_settings = apply_agent_settings_diff(org_settings, member_diff)
        llm = getattr(effective_settings, 'llm', None)
        if llm is None:
            return None
        return managed_llm_key_config_from_model(
            llm.model,
            llm.base_url,
        )

    @classmethod
    async def repair_member(cls, org_id: UUID, user_id: UUID) -> str:
        async with a_session_maker() as session:
            async with budget_control_session(
                budget_engine(session), org_id
            ) as control:
                member = await control.session.get(
                    OrgMember,
                    {'org_id': org_id, 'user_id': user_id},
                    with_for_update=True,
                )
                if member is None or (
                    member.managed_llm_key_ownership_version
                    >= MANAGED_LLM_KEY_OWNERSHIP_VERSION
                ):
                    return 'skipped'
                org = await control.session.get(Org, org_id)
                if org is None:
                    return 'skipped'
                config = cls._effective_managed_key_config(org, member)
                if org._llm_api_key or member.has_custom_llm_api_key or config is None:
                    member.managed_llm_key_ownership_version = (
                        MANAGED_LLM_KEY_OWNERSHIP_VERSION
                    )
                    await control.session.commit()
                    return 'skipped'

                existing_key = (
                    member.llm_api_key.get_secret_value() if member._llm_api_key else ''
                )
                if existing_key and await LiteLlmManager.verify_existing_key_strict(
                    existing_key,
                    str(user_id),
                    str(org_id),
                    openhands_type=config.openhands_type,
                ):
                    member.managed_llm_key_ownership_version = (
                        MANAGED_LLM_KEY_OWNERSHIP_VERSION
                    )
                    await control.session.commit()
                    return 'verified'

                pending = await control.pending_operation()
                if pending is not None and str(user_id) in pending.plan.get(
                    'added_member_ids', []
                ):
                    from server.services.managed_budget_service import (
                        ManagedBudgetService,
                    )

                    result = await ManagedBudgetService(control.engine).maintain(org_id)
                    if result['status'] != 'applied':
                        raise BudgetWriteDenied(
                            'Member allowance is still pending verification'
                        )
                new_key = await LiteLlmManager.generate_key(
                    str(user_id),
                    str(org_id),
                    get_openhands_cloud_key_alias(str(user_id), str(org_id)),
                    {'type': 'openhands'} if config.openhands_type else None,
                )
                if not await LiteLlmManager.verify_existing_key_strict(
                    new_key,
                    str(user_id),
                    str(org_id),
                    openhands_type=config.openhands_type,
                ):
                    raise RuntimeError(
                        'Generated LiteLLM key failed ownership verification'
                    )
                member.llm_api_key = new_key
                member.has_custom_llm_api_key = False
                member.managed_llm_key_ownership_version = (
                    MANAGED_LLM_KEY_OWNERSHIP_VERSION
                )
                await control.session.commit()
                return 'repaired'

    async def __call__(self, task: MaintenanceTask) -> dict:
        del task
        counts = {'verified': 0, 'repaired': 0, 'skipped': 0}
        errors: list[dict[str, str]] = []
        for target in self.targets:
            try:
                org_id, user_id = UUID(target.org_id), UUID(target.user_id)
            except ValueError:
                errors.append(
                    {
                        'org_id': target.org_id,
                        'user_id': target.user_id,
                        'error': 'invalid_uuid',
                    }
                )
                continue
            try:
                counts[await self.repair_member(org_id, user_id)] += 1
            except Exception as exc:
                logger.exception(
                    'managed_llm_key_ownership_repair_failed',
                    extra={'org_id': target.org_id, 'user_id': target.user_id},
                )
                errors.append(
                    {
                        'org_id': target.org_id,
                        'user_id': target.user_id,
                        'error': str(exc),
                    }
                )
        return {
            **counts,
            'error_count': len(errors),
            'errors': errors[:20],
        }


def enqueue_managed_llm_key_ownership_tasks(
    batch_size: int = MANAGED_KEY_REPAIR_BATCH_SIZE,
) -> int:
    """Queue stale member rows, retrying only rows not yet reconciled."""
    with session_maker() as session:
        processor_type = (
            f'{ManagedLlmKeyOwnershipProcessor.__module__}.'
            f'{ManagedLlmKeyOwnershipProcessor.__name__}'
        )
        existing = (
            session.query(MaintenanceTask)
            .filter(
                MaintenanceTask.status.in_(
                    [MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING]
                ),
                MaintenanceTask.processor_type == processor_type,
            )
            .count()
        )
        if existing:
            return 0

        rows = (
            session.query(OrgMember.org_id, OrgMember.user_id)
            .filter(
                OrgMember.managed_llm_key_ownership_version
                < MANAGED_LLM_KEY_OWNERSHIP_VERSION
            )
            .order_by(OrgMember.org_id, OrgMember.user_id)
            .all()
        )
        targets = [
            ManagedLlmKeyOwnershipTarget(
                org_id=str(row.org_id), user_id=str(row.user_id)
            )
            for row in rows
        ]
        for offset in range(0, len(targets), batch_size):
            processor = ManagedLlmKeyOwnershipProcessor(
                targets=targets[offset : offset + batch_size]
            )
            task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
            task.set_processor(processor)
            session.add(task)
        session.commit()
        return len(targets)
