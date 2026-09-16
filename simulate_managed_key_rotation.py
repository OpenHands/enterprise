"""TEST-ONLY: reproduce the PR #353 managed-key rotation incident.

This entrypoint deliberately recreates the production regression that PR #406
(migration 164) fixes. For every personal-org member it rotates the managed
LiteLLM key exactly the way ``ManagedLlmKeyOwnershipProcessor`` does --
delete the deterministic alias, generate a fresh key, write it onto
``org_member._llm_api_key`` and bump ``managed_llm_key_ownership_version`` --
but it INTENTIONALLY DOES NOT propagate the new key into the
``user_settings.llm_api_key`` cache. That skipped propagation is the #353 bug.

Running this against a preview leaves the DB in the exact "broken" state:
``org_member`` holds a fresh, valid key while ``user_settings`` still points at
the now-deleted old key. Deploying the PR #406 image (migration 164) should
then re-sync the cache.

DO NOT MERGE / DO NOT RUN IN SHARED ENVIRONMENTS. It calls the real LiteLLM
management API and rotates live keys. It is meant for a single-user feature
preview only.

Run with:  python -m simulate_managed_key_rotation
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from openhands.sdk.settings import apply_agent_settings_diff
from server.logger import logger
from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager, get_openhands_cloud_key_alias
from storage.org import Org
from storage.org_member import MANAGED_LLM_KEY_OWNERSHIP_VERSION, OrgMember
from storage.org_store import OrgStore
from storage.saas_settings_store import managed_llm_key_config_from_model


def _effective_managed_key_config(org: Org, member: OrgMember):
    org_settings = OrgStore.get_agent_settings_from_org(org)
    member_diff = dict(member.agent_settings_diff or {})
    member_diff.pop('mcp_config', None)
    effective_settings = apply_agent_settings_diff(org_settings, member_diff)
    llm = getattr(effective_settings, 'llm', None)
    if llm is None:
        return None
    return managed_llm_key_config_from_model(llm.model, llm.base_url)


async def main() -> None:
    rotated = 0
    skipped = 0
    errors = 0

    async with a_session_maker() as session:
        # Personal orgs only (org_id == user_id): the single-member org whose
        # managed key the user_settings cache mirrors -- same scope as #353.
        result = await session.execute(
            select(OrgMember).where(OrgMember.org_id == OrgMember.user_id)
        )
        members = result.scalars().all()
        logger.info('simulate_353: personal-org members', extra={'count': len(members)})

        for member in members:
            user_id = str(member.user_id)
            org_id = str(member.org_id)
            try:
                org = await session.get(Org, member.org_id)
                if org is None:
                    skipped += 1
                    continue

                config = _effective_managed_key_config(org, member)
                # Only rotate genuinely managed keys, mirroring #353's guard.
                if org._llm_api_key or member.has_custom_llm_api_key or config is None:
                    skipped += 1
                    continue

                key_alias = get_openhands_cloud_key_alias(user_id, org_id)
                await LiteLlmManager.delete_key_by_alias_strict(key_alias=key_alias)
                new_key = await LiteLlmManager.generate_key(
                    user_id,
                    org_id,
                    key_alias,
                    {'type': 'openhands'} if config.openhands_type else None,
                )
                if not await LiteLlmManager.verify_existing_key_strict(
                    new_key,
                    user_id,
                    org_id,
                    openhands_type=config.openhands_type,
                ):
                    raise RuntimeError('Generated LiteLLM key failed verification')

                member.llm_api_key = new_key
                member.has_custom_llm_api_key = False
                member.managed_llm_key_ownership_version = (
                    MANAGED_LLM_KEY_OWNERSHIP_VERSION
                )
                # BUG BEING SIMULATED: #353 stopped here and never updated the
                # user_settings.llm_api_key cache. We intentionally do the same.
                await session.commit()
                rotated += 1
                logger.info(
                    'simulate_353: rotated org_member key', extra={'user_id': user_id}
                )
            except Exception:
                await session.rollback()
                errors += 1
                logger.exception(
                    'simulate_353: rotation failed', extra={'user_id': user_id}
                )

    logger.info(
        'simulate_353: done',
        extra={'rotated': rotated, 'skipped': skipped, 'errors': errors},
    )
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
