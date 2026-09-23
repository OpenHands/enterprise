"""Apply member policy before issuing a managed key."""

from datetime import UTC, datetime
from math import isfinite
from uuid import UUID

import httpx
from sqlalchemy import select

from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_budget_utils import budget_values_match
from storage.org_member import OrgMember


def _spend(member: dict) -> float:
    value = member.get('spend')
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not isfinite(value)
        or value < 0
    ):
        raise RuntimeError('Member budget readback has invalid spend')
    return float(value)


async def provision_budget_member(
    client: httpx.AsyncClient, org_id: str, user_id: str
) -> bool:
    if org_id == user_id:
        return False
    try:
        org_uuid, user_uuid = UUID(org_id), UUID(user_id)
    except ValueError:
        return False

    async with a_session_maker() as session:
        store = OrgBudgetStore(session)
        settings = await store.get_settings(org_uuid)
        if settings is None or not settings.enabled:
            return False
        existing_member = (
            await session.get(OrgMember, (org_uuid, user_uuid)) is not None
        )
        override = await store.get_override(org_uuid, user_uuid)
        limit = settings.default_user_monthly_limit
        if override is not None:
            limit = None if override.is_disabled else override.monthly_limit
        cycle_start = settings.cycle_start_at
        baselines = await store.get_cycle_baselines(org_uuid, cycle_start)
        stored_baseline = baselines.get(
            user_id, (settings.user_cycle_start_spend or {}).get(user_id, 0.0)
        )

    native = await LiteLlmManager._get_team_members_financial_data(
        client, org_id, unkeyed_member_id=user_id if not existing_member else None
    )
    if not native:
        raise RuntimeError('Member budget readback is incomplete')
    previous = native['members'].get(user_id)
    counter_reset = previous is not None and _spend(previous) < stored_baseline
    if existing_member and previous is not None and not counter_reset:
        return True

    baseline = stored_baseline if previous is not None else 0.0
    if previous is not None and baseline > _spend(previous):
        baseline = 0.0
    cap = baseline + limit if limit is not None else None
    if previous is None:
        await LiteLlmManager._add_user_to_team(client, user_id, org_id, cap)
    elif (
        previous['uses_shared_budget'] != (cap is None)
        or cap is not None
        and not budget_values_match(previous['max_budget'], cap)
    ):
        await LiteLlmManager._update_user_in_team(
            client, user_id, org_id, cap, clear_budget=cap is None
        )
    native = await LiteLlmManager._get_team_members_financial_data(
        client, org_id, unkeyed_member_id=user_id
    )
    member = native.get('members', {}).get(user_id)
    if member is None:
        raise RuntimeError('Budget member is missing from LiteLLM')
    observed_spend = _spend(member)
    if previous is None or counter_reset:
        baseline = observed_spend
        cap = baseline + limit if limit is not None else None
    actual = member['max_budget']
    shared = member['uses_shared_budget']
    if (cap is None and not shared) or (
        cap is not None and (shared or not budget_values_match(actual, cap))
    ):
        raise RuntimeError('Member budget was not applied in LiteLLM')

    # Remote reads/writes finish before the short settings-row lock is acquired.
    async with a_session_maker() as session:
        result = await session.execute(
            select(OrgBudgetSettings)
            .where(OrgBudgetSettings.org_id == org_uuid)
            .with_for_update()
        )
        current = result.scalar_one_or_none()
        store = OrgBudgetStore(session)
        override = await store.get_override(org_uuid, user_uuid)
        current_limit = current.default_user_monthly_limit if current else None
        if override is not None:
            current_limit = None if override.is_disabled else override.monthly_limit
        if (
            current is None
            or not current.enabled
            or current.cycle_start_at != cycle_start
            or current_limit != limit
        ):
            raise RuntimeError(
                'Budget policy changed during member provisioning; retry'
            )
        if (
            not existing_member
            and previous is not None
            and await session.get(OrgMember, (org_uuid, user_uuid)) is not None
        ):
            return True
        await store.record_cycle_baselines(
            org_uuid,
            cycle_start,
            {user_id: baseline},
            source=OrgBudgetCycleBaseline.SOURCE_MEMBER_ADDED,
            observed_at=datetime.now(UTC),
            replace=previous is None or baseline != stored_baseline,
        )
        current.user_cycle_start_spend = {
            **(current.user_cycle_start_spend or {}),
            user_id: baseline,
        }
        await session.commit()
    return True
