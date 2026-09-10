from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from openhands.app_server.utils.jsonpatch_compat import deep_merge
from server.logger import logger
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
        org_settings = OrgStore.get_agent_settings_from_org(org).model_dump(mode='json')
        member_diff = dict(member.agent_settings_diff or {})
        member_diff.pop('mcp_config', None)
        merged_settings = deep_merge(org_settings, member_diff)
        llm = merged_settings.get('llm')
        if not isinstance(llm, dict):
            return None
        return managed_llm_key_config_from_model(
            llm.get('model'),
            llm.get('base_url'),
        )

    async def __call__(self, task: MaintenanceTask) -> dict:
        del task
        verified = 0
        repaired = 0
        skipped = 0
        errors: list[dict[str, str]] = []

        async with a_session_maker() as session:
            for target in self.targets:
                try:
                    org_id = UUID(target.org_id)
                    user_id = UUID(target.user_id)
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
                    member = await session.get(
                        OrgMember,
                        {'org_id': org_id, 'user_id': user_id},
                        with_for_update=True,
                    )
                    if member is None:
                        skipped += 1
                        continue
                    if (
                        member.managed_llm_key_ownership_version
                        >= MANAGED_LLM_KEY_OWNERSHIP_VERSION
                    ):
                        skipped += 1
                        continue

                    org = await session.get(Org, org_id)
                    if org is None:
                        skipped += 1
                        continue

                    config = self._effective_managed_key_config(org, member)
                    if (
                        org._llm_api_key
                        or member.has_custom_llm_api_key
                        or config is None
                    ):
                        member.managed_llm_key_ownership_version = (
                            MANAGED_LLM_KEY_OWNERSHIP_VERSION
                        )
                        await session.commit()
                        skipped += 1
                        continue

                    existing_key = member.llm_api_key.get_secret_value()
                    owned = await LiteLlmManager.verify_existing_key_strict(
                        existing_key,
                        target.user_id,
                        target.org_id,
                        openhands_type=config.openhands_type,
                    )
                    if owned:
                        member.managed_llm_key_ownership_version = (
                            MANAGED_LLM_KEY_OWNERSHIP_VERSION
                        )
                        await session.commit()
                        verified += 1
                        continue

                    # Delete only this member's deterministic alias. The raw key
                    # currently stored on the row may belong to another user and
                    # must remain valid for that correct owner.
                    key_alias = get_openhands_cloud_key_alias(
                        target.user_id,
                        target.org_id,
                    )
                    await LiteLlmManager.delete_key_by_alias_strict(key_alias=key_alias)
                    new_key = await LiteLlmManager.generate_key(
                        target.user_id,
                        target.org_id,
                        key_alias,
                        {'type': 'openhands'} if config.openhands_type else None,
                    )
                    if not await LiteLlmManager.verify_existing_key_strict(
                        new_key,
                        target.user_id,
                        target.org_id,
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
                    await session.commit()
                    repaired += 1
                except Exception as exc:
                    await session.rollback()
                    logger.exception(
                        'managed_llm_key_ownership_repair_failed',
                        extra={
                            'org_id': target.org_id,
                            'user_id': target.user_id,
                        },
                    )
                    errors.append(
                        {
                            'org_id': target.org_id,
                            'user_id': target.user_id,
                            'error': str(exc),
                        }
                    )

        return {
            'verified': verified,
            'repaired': repaired,
            'skipped': skipped,
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
