"""Explicit budget takeover and replay of a committed adoption operation."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from server.services.budget_adoption_plan import (
    BudgetAdoptionRequest,
    BudgetAdoptionUnsupported,
    adoption_fingerprint,
    build_adoption_plan,
    validate_adoption_observation,
)
from storage.budget_control import (
    BudgetControlConflict,
    BudgetControlSession,
    BudgetWriteDenied,
    budget_control_session,
    budget_request_hash,
)
from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_member import OrgMember
from storage.user import User


class BudgetAdoptionService:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def _member_ids(self, control: BudgetControlSession) -> set[str]:
        result = await control.session.scalars(
            select(OrgMember.user_id).where(OrgMember.org_id == control.org_id)
        )
        return {str(user_id) for user_id in result}

    async def _settings(self, control: BudgetControlSession) -> OrgBudgetSettings:
        if await control.session.scalar(
            select(User.id).where(User.id == control.org_id)
        ):
            raise BudgetAdoptionUnsupported(
                'Personal workspaces cannot adopt organization budgets'
            )
        settings = await control.session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == control.org_id)
        )
        if settings is None:
            settings = OrgBudgetSettings(org_id=control.org_id, control_mode='external')
            control.session.add(settings)
            await control.session.commit()
        return settings

    async def _observe(self, org_id: UUID) -> dict[str, Any]:
        return await LiteLlmManager.get_team_members_financial_data(
            str(org_id), include_control_policy=True
        )

    async def preview(self, org_id: UUID) -> dict[str, Any]:
        async with budget_control_session(self.engine, org_id) as control:
            settings = await self._settings(control)
            if settings.control_mode == 'managed':
                raise BudgetControlConflict('Organization is already managed')
            members = await self._member_ids(control)
            observation = await self._observe(org_id)
            return {
                'control_mode': settings.control_mode,
                'generation': settings.control_generation,
                'fingerprint': adoption_fingerprint(
                    org_id, settings.control_generation, members, observation
                ),
                'observation': observation,
                'org_member_ids': sorted(members),
                'unmapped_member_ids': sorted(
                    set(observation['member_counters']) - members
                ),
                'pending_operation_id': (
                    str(pending.id)
                    if (pending := await control.pending_operation())
                    else None
                ),
                'observed_at': datetime.now(UTC).isoformat(),
            }

    async def confirm(
        self, org_id: UUID, actor: str, request: BudgetAdoptionRequest
    ) -> dict[str, Any]:
        request_hash = budget_request_hash(
            {'actor': actor, **request.model_dump(mode='json')}
        )
        async with budget_control_session(self.engine, org_id) as control:
            settings = await self._settings(control)
            operation = await control.find_operation(
                request.idempotency_key, request_hash
            )
            if operation is None:
                if await control.pending_operation() is not None:
                    raise BudgetControlConflict(
                        'Retry or hand off the existing adoption first'
                    )
                members = await self._member_ids(control)
                observation = await self._observe(org_id)
                plan = build_adoption_plan(
                    org_id, settings.control_generation, members, observation, request
                )
                operation = await control.reserve_operation(
                    idempotency_key=request.idempotency_key,
                    request_hash=request_hash,
                    kind='adopt',
                    actor=actor,
                    plan=plan,
                )
            if operation.status == 'abandoned':
                raise BudgetControlConflict(
                    'This operation was handed off; review a new preview'
                )
            if operation.status == 'pending':
                await self._execute(control, operation)
            return await self._result(control, operation)

    async def retry(self, org_id: UUID) -> dict[str, Any] | None:
        async with budget_control_session(self.engine, org_id) as control:
            await self._settings(control)
            operation = await control.pending_operation()
            if operation is None:
                return None
            if operation.kind != 'adopt':
                raise BudgetControlConflict('Pending operation is not an adoption')
            await self._execute(control, operation)
            return await self._result(control, operation)

    async def hand_off(self, org_id: UUID, actor: str) -> dict[str, Any]:
        async with budget_control_session(self.engine, org_id) as control:
            await self._settings(control)
            await control.hand_off(actor)
            settings = await control.settings()
            return {
                'control_mode': settings.control_mode,
                'generation': settings.control_generation,
            }

    async def _result(
        self, control: BudgetControlSession, operation: OrgBudgetOperation
    ) -> dict[str, Any]:
        settings = await control.settings()
        return {
            'operation_id': str(operation.id),
            'status': operation.status,
            'control_mode': settings.control_mode,
            'generation': settings.control_generation,
            'error': operation.last_error,
        }

    async def _execute(
        self, control: BudgetControlSession, operation: OrgBudgetOperation
    ) -> None:
        await control.require_executable(operation)
        plan = operation.plan
        try:
            if set(plan['member_baselines']) != await self._member_ids(control):
                raise BudgetControlConflict(
                    'Membership changed during adoption; hand off and preview again'
                )
            observation = await self._observe(control.org_id)
            self._check_counter_continuity(plan, observation)
            self._check_preserved_policy(plan, observation)
            for write in plan['writes']:
                await LiteLlmManager.apply_budget_write(
                    control.org_id, operation.id, **write
                )
            readback = await self._observe(control.org_id)
            self._check_counter_continuity(plan, readback, after_write=True)
            self._verify_targets(plan, readback)
            self._check_preserved_policy(plan, readback)
        except Exception as error:
            await control.record_failure(operation, f'{type(error).__name__}: {error}')
            return

        async def apply_settings(settings: OrgBudgetSettings) -> None:
            store = OrgBudgetStore(control.session)
            future = plan['future_policy']
            current = plan['current_allowances']
            settings.enabled = True
            settings.monthly_limit = future['monthly_limit']
            settings.default_user_monthly_limit = future['default_user_monthly_limit']
            settings.reset_day = future['reset_day']
            settings.cycle_start_at = datetime.fromisoformat(plan['cycle_start_at'])
            settings.cycle_end_at = datetime.fromisoformat(plan['cycle_end_at'])
            settings.cycle_start_spend = plan['team_baseline']
            settings.user_cycle_start_spend = plan['member_baselines']
            settings.cycle_allowance = current['team']
            settings.cycle_default_user_allowance = current['default_member']
            settings.cycle_user_allowances = current['members']
            for override in await store.get_overrides(control.org_id):
                await store.delete_override(override)
            for user_id, limit in future['member_limits'].items():
                await store.upsert_override(
                    control.org_id, UUID(user_id), limit, limit is None
                )
            await store.record_cycle_baselines(
                control.org_id,
                settings.cycle_start_at,
                plan['member_baselines'],
                source='adoption',
                observed_at=datetime.fromisoformat(plan['observed_at']),
            )
            settings.litellm_known_member_ids = sorted(plan['member_baselines'])
            settings.litellm_last_sync_at = datetime.now(UTC)
            settings.litellm_last_sync_status = 'success'
            settings.litellm_last_sync_error = None

        await control.finish_operation(operation, apply_settings)

    def _check_counter_continuity(
        self,
        plan: dict[str, Any],
        observation: dict[str, Any],
        *,
        after_write: bool = False,
    ) -> None:
        validate_adoption_observation(observation, set(plan['member_baselines']))
        if observation.get('team_reset_known') is not True:
            raise BudgetWriteDenied('Team counter reset schedule is unknown')
        spend = observation.get('team_spend')
        if not isinstance(spend, int | float) or spend < plan['team_baseline']:
            raise BudgetWriteDenied('Team spend counter reset during adoption')
        initial_policy = plan['preserved_policy']['team']
        for field, observed_name in (
            ('budget_duration', 'team_budget_duration'),
            ('budget_reset_at', 'team_budget_reset_at'),
        ):
            allowed = (None,) if after_write else (None, initial_policy.get(field))
            if observation.get(observed_name) not in allowed:
                raise BudgetWriteDenied('Team reset epoch changed during adoption')
        for user_id, baseline in plan['member_baselines'].items():
            counter = observation.get('member_counters', {}).get(user_id)
            if not counter or counter.get('reset_known') is not True:
                raise BudgetWriteDenied('Member counter is missing or unknown')
            if counter.get('spend', -1) < baseline:
                raise BudgetWriteDenied('Member spend counter reset during adoption')
            initial_counter = plan['counter_identities'][user_id]
            if (
                counter['source'] == 'new_membership'
                and initial_counter['source'] == 'membership'
            ):
                raise BudgetWriteDenied(
                    'Existing membership was removed during adoption'
                )
            if (
                initial_counter['budget_source'] == 'private_member'
                and counter['budget_id'] != initial_counter['budget_id']
                and not (
                    counter['budget_id'] is None
                    and plan['expected_member_caps'][user_id] is None
                )
            ):
                raise BudgetWriteDenied(
                    'Private member budget identity changed during adoption'
                )
            for field in ('budget_duration', 'budget_reset_at'):
                initial = plan['counter_identities'][user_id].get(field)
                if counter.get(field) not in (
                    (None,) if after_write else (None, initial)
                ):
                    raise BudgetWriteDenied(
                        'Member reset epoch changed during adoption'
                    )

    def _check_preserved_policy(
        self, plan: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        original = plan['preserved_policy']
        current = observation['control_policy']
        if current['team'].get('max_budget') not in (
            original['team'].get('max_budget'),
            plan['expected_team_cap'],
        ):
            raise BudgetWriteDenied(
                'Team budget was changed outside the pending operation'
            )
        owned = {'max_budget', 'budget_duration', 'budget_reset_at'}

        def independent(policy: dict[str, Any]) -> dict[str, Any]:
            return {
                key: (
                    []
                    if value is None and key in {'models', 'allowed_models'}
                    else value
                )
                for key, value in policy.items()
                if key not in owned
            }

        if independent(original['team']) != independent(current['team']):
            raise BudgetWriteDenied(
                'Independent team restrictions changed during adoption'
            )
        for user_id in plan['member_baselines']:
            before = original['members'].get(user_id, {})
            after = current['members'].get(user_id, {})
            fields = before.keys() | after.keys()
            if independent(
                {field: before.get(field) for field in fields}
            ) != independent({field: after.get(field) for field in fields}):
                raise BudgetWriteDenied(
                    'Independent member restrictions changed during adoption'
                )
        if original['keys'] != current['keys']:
            raise BudgetWriteDenied('Key policy changed during adoption')

    def _verify_targets(
        self, plan: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        if observation.get('team_max_budget') != plan['expected_team_cap']:
            raise BudgetWriteDenied(
                'Team cap readback did not match the committed target'
            )
        default = observation.get('default_member_budget') or {}
        if any(
            default.get(field) is not None
            for field in ('max_budget', 'budget_duration', 'budget_reset_at')
        ):
            raise BudgetWriteDenied('Default member budget was not cleared')
        for user_id, target in plan['expected_member_caps'].items():
            member = observation['members'][user_id]
            if target is None:
                matches = member['uses_shared_budget'] is True
            else:
                matches = (
                    member['uses_shared_budget'] is False
                    and member['max_budget'] == target
                )
            if not matches:
                raise BudgetWriteDenied(
                    f'Member {user_id} cap readback did not match the committed target'
                )
