"""Apply an organization's member policy before issuing a new member's key."""

from datetime import UTC, datetime
from math import isclose
from uuid import UUID

import httpx
from sqlalchemy import select


async def provision_budget_member(
    client: httpx.AsyncClient, org_id: str, user_id: str
) -> bool:
    from storage.database import a_session_maker
    from storage.lite_llm_manager import LiteLlmManager
    from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
    from storage.org_budget_settings import OrgBudgetSettings
    from storage.org_budget_store import OrgBudgetStore
    from storage.org_member import OrgMember

    if org_id == user_id:
        return False
    try:
        org_uuid, user_uuid = UUID(org_id), UUID(user_id)
    except ValueError:
        return False

    async with a_session_maker() as session:
        result = await session.execute(
            select(OrgBudgetSettings)
            .where(OrgBudgetSettings.org_id == org_uuid)
            .with_for_update()
        )
        settings = result.scalar_one_or_none()
        if settings is None or not settings.enabled:
            return False
        if await session.get(OrgMember, (org_uuid, user_uuid)) is not None:
            # Existing member/key refresh keeps its established native cap.
            return False

        store = OrgBudgetStore(session)
        override = await store.get_override(org_uuid, user_uuid)
        limit = settings.default_user_monthly_limit
        if override is not None:
            limit = None if override.is_disabled else override.monthly_limit
        baselines = await store.get_cycle_baselines(org_uuid, settings.cycle_start_at)
        baseline = baselines.get(
            user_id, (settings.user_cycle_start_spend or {}).get(user_id, 0.0)
        )
        cap = baseline + limit if limit is not None else None
        await LiteLlmManager._add_user_to_team(client, user_id, org_id, cap)
        native = await LiteLlmManager._get_team(client, org_id)
        if native is None or not isinstance(native.get('team_memberships'), list):
            raise RuntimeError('New member budget readback is incomplete')
        team = native['team_info']
        member = next(
            (m for m in native['team_memberships'] if m.get('user_id') == user_id),
            None,
        )
        roster = team.get('members_with_roles') or []
        if member is None and not any(m.get('user_id') == user_id for m in roster):
            raise RuntimeError('New budget member is missing from LiteLLM')
        actual = None
        if member is not None and member.get('budget_id') is not None:
            actual = member['litellm_budget_table']['max_budget']
        elif (team.get('metadata') or {}).get('team_member_budget_id') is not None:
            raise RuntimeError('New member inherited an unverified native budget')
        if (cap is None and actual is not None) or (
            cap is not None
            and (
                isinstance(actual, bool)
                or not isinstance(actual, int | float)
                or not isclose(actual, cap, abs_tol=1e-9)
            )
        ):
            raise RuntimeError('New member budget was not applied in LiteLLM')

        # Persist before returning a usable key, so maintenance cannot forgive usage.
        await store.record_cycle_baselines(
            org_uuid,
            settings.cycle_start_at,
            {user_id: baseline},
            source=OrgBudgetCycleBaseline.SOURCE_MEMBER_ADDED,
            observed_at=datetime.now(UTC),
        )
        settings.user_cycle_start_spend = {
            **(settings.user_cycle_start_spend or {}),
            user_id: baseline,
        }
        await session.commit()
        return True
