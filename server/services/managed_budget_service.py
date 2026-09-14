"""Journaled policy edits and renewals for an explicitly adopted organization."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from server.services.budget_adoption_plan import (
    Allowance,
    BudgetAdoptionUnsupported,
    PositiveAllowance,
    budget_writes,
    next_budget_reset,
    validate_adoption_observation,
)
from server.services.budget_adoption_service import BudgetAdoptionService
from storage.budget_control import (
    BudgetControlConflict,
    BudgetControlSession,
    BudgetWriteDenied,
    budget_control_session,
    budget_request_hash,
)
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore


class ManagedBudgetUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_generation: int = Field(ge=0)
    enabled: bool
    current_cycle_team_allowance: PositiveAllowance
    current_cycle_default_member_allowance: Allowance | None
    current_cycle_member_allowances: dict[str, Allowance | None] = Field(
        default_factory=dict
    )
    future_monthly_limit: PositiveAllowance
    future_default_member_limit: PositiveAllowance | None
    future_member_limits: dict[str, PositiveAllowance | None] = Field(
        default_factory=dict
    )
    reset_day: Literal[1, 15]


class ManagedBudgetService(BudgetAdoptionService):
    async def update(
        self, org_id: UUID, actor: str, request: ManagedBudgetUpdate
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
                if settings.control_generation != request.expected_generation:
                    raise BudgetControlConflict(
                        'Budget policy changed; refresh before saving'
                    )
                previous = await self._verified_operation(control)
                members = await self._member_ids(control)
                if (
                    request.current_cycle_member_allowances.keys()
                    | request.future_member_limits.keys()
                ) - members:
                    raise BudgetAdoptionUnsupported(
                        'Allowances contain users outside the organization'
                    )
                observation = await self._observe(org_id)
                self._check_managed_observation(previous, observation, members)
                plan = self._plan(settings, observation, members)
                plan['enabled'] = request.enabled
                plan['current_allowances'] = {
                    'team': request.current_cycle_team_allowance,
                    'default_member': request.current_cycle_default_member_allowance,
                    'members': request.current_cycle_member_allowances,
                }
                plan['future_policy'] = {
                    'monthly_limit': request.future_monthly_limit,
                    'default_user_monthly_limit': request.future_default_member_limit,
                    'member_limits': request.future_member_limits,
                    'reset_day': request.reset_day,
                }
                self._set_targets(org_id, plan, observation)
                operation = await control.reserve_operation(
                    idempotency_key=request.idempotency_key,
                    request_hash=request_hash,
                    kind='settings',
                    actor=actor,
                    plan=plan,
                )
            if operation.status == 'abandoned':
                raise BudgetControlConflict(
                    'Operation was handed off; review the current policy'
                )
            if operation.status == 'pending':
                await self._execute(control, operation)
            return await self._result(control, operation)

    async def maintain(
        self, org_id: UUID, *, now: datetime | None = None
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError('Maintenance time must be timezone-aware')
        async with budget_control_session(self.engine, org_id) as control:
            settings = await self._settings(control)
            pending = await control.pending_operation()
            if pending is not None:
                await self._execute(control, pending)
                if pending.status == 'applied':
                    await self._after_maintenance(control, pending.verification)
                return await self._result(control, pending)
            if settings.control_mode != 'managed':
                return {
                    'status': 'skipped',
                    'control_mode': settings.control_mode,
                    'cycle_rolled': False,
                }
            previous = await self._verified_operation(control)
            cycle_end = settings.cycle_end_at
            if cycle_end is None:
                raise BudgetWriteDenied('Current cycle has not been adopted')
            members = await self._member_ids(control)
            observation = await self._observe(org_id)
            self._check_managed_observation(previous, observation, members)
            if not settings.enabled or now < cycle_end:
                await self._after_maintenance(control, observation)
                return {
                    'status': 'healthy',
                    'control_mode': 'managed',
                    'cycle_rolled': False,
                }

            plan = self._plan(settings, observation, members, now=now)
            store = OrgBudgetStore(control.session)
            overrides = await store.get_overrides(org_id)
            future = {
                'monthly_limit': settings.monthly_limit,
                'default_user_monthly_limit': settings.default_user_monthly_limit,
                'member_limits': {
                    str(o.user_id): None if o.is_disabled else o.monthly_limit
                    for o in overrides
                },
                'reset_day': settings.reset_day,
            }
            # Renew once at the observation; never invent historical counter boundaries.
            plan['previous_cycle_end_at'] = cycle_end.isoformat()
            plan['cycle_start_at'] = now.isoformat()
            plan['cycle_end_at'] = next_budget_reset(
                now, settings.reset_day
            ).isoformat()
            plan['team_baseline'] = observation['team_spend']
            plan['member_baselines'] = {
                u: observation['member_counters'][u]['spend'] for u in members
            }
            plan['future_policy'] = future
            plan['current_allowances'] = {
                'team': future['monthly_limit'],
                'default_member': future['default_user_monthly_limit'],
                'members': future['member_limits'],
            }
            self._set_targets(org_id, plan, observation)
            key = f'rollover:{settings.control_generation}:{cycle_end.isoformat()}'
            operation = await control.reserve_operation(
                idempotency_key=key,
                request_hash=budget_request_hash({'key': key}),
                kind='rollover',
                actor='budget-maintenance',
                plan=plan,
            )
            await self._execute(control, operation)
            if operation.status == 'applied':
                await self._after_maintenance(control, operation.verification)
            result = await self._result(control, operation)
            result['cycle_rolled'] = operation.status == 'applied'
            return result

    async def _after_maintenance(
        self, control: BudgetControlSession, observation: dict[str, Any] | None
    ) -> None:
        from server.services.org_budget_service import (
            OrgBudgetService,
            _parse_litellm_financial_snapshot,
        )

        if observation is None:
            raise BudgetWriteDenied('A verified financial observation is required')
        settings = await control.settings()
        service = OrgBudgetService(control.session)
        snapshot = _parse_litellm_financial_snapshot(observation)
        await service._cache_financial_snapshot(settings, snapshot)
        await service._record_litellm_sync(settings, 'success')
        await service._maybe_send_alerts(
            control.org_id,
            settings,
            await service._get_thresholds(control.org_id),
            max(snapshot.team_spend - settings.cycle_start_spend, 0),
            settings.cycle_start_at,
        )
        await control.session.commit()

    async def _verified_operation(
        self, control: BudgetControlSession
    ) -> OrgBudgetOperation:
        settings = await control.settings()
        if settings.control_mode != 'managed':
            raise BudgetWriteDenied('Adopt the organization before changing its budget')
        if await control.pending_operation() is not None:
            raise BudgetControlConflict('Retry or hand off the pending operation first')
        previous = await control.session.scalar(
            select(OrgBudgetOperation)
            .where(
                OrgBudgetOperation.org_id == control.org_id,
                OrgBudgetOperation.status == 'applied',
            )
            .order_by(OrgBudgetOperation.generation.desc())
            .limit(1)
        )
        if (
            previous is None
            or previous.verification is None
            or settings.cycle_end_at is None
        ):
            raise BudgetWriteDenied(
                'A verified adoption and counter identity are required'
            )
        if previous.generation != settings.control_generation:
            raise BudgetWriteDenied('Budget generation has no verified operation')
        plan = previous.plan
        if (
            settings.cycle_start_spend != plan['team_baseline']
            or settings.user_cycle_start_spend != plan['member_baselines']
            or settings.cycle_start_at.isoformat() != plan['cycle_start_at']
            or settings.cycle_end_at.isoformat() != plan['cycle_end_at']
            or settings.cycle_allowance != plan['current_allowances']['team']
            or settings.cycle_default_user_allowance
            != plan['current_allowances']['default_member']
            or settings.cycle_user_allowances != plan['current_allowances']['members']
        ):
            raise BudgetWriteDenied(
                'Current-cycle policy differs from its verified operation'
            )
        return previous

    def _check_managed_observation(
        self,
        previous: OrgBudgetOperation,
        observation: dict[str, Any],
        members: set[str],
    ) -> None:
        validate_adoption_observation(observation, members)
        if members != set(previous.plan['member_baselines']):
            raise BudgetControlConflict(
                'Membership changed; a journaled membership repair is required'
            )
        self._verify_targets(previous.plan, observation)
        self._check_counter_continuity(previous.plan, observation, after_write=True)
        assert previous.verification is not None
        if observation['team_spend'] < previous.verification['team_spend']:
            raise BudgetWriteDenied('Team counter fell below its verified observation')
        for user_id in members:
            before = previous.verification['member_counters'][user_id]
            after = observation['member_counters'][user_id]
            if after['spend'] < before['spend']:
                raise BudgetWriteDenied(
                    'Member counter fell below its verified observation'
                )
            if {k: v for k, v in before.items() if k != 'spend'} != {
                k: v for k, v in after.items() if k != 'spend'
            }:
                raise BudgetWriteDenied(
                    'Membership counter identity changed; automatic reanchoring is unsafe'
                )

    def _plan(
        self,
        settings: OrgBudgetSettings,
        observation: dict[str, Any],
        members: set[str],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if settings.cycle_end_at is None:
            raise BudgetWriteDenied('Current cycle has not been adopted')
        if not members <= (settings.user_cycle_start_spend or {}).keys():
            raise BudgetWriteDenied('Current-cycle member baselines are incomplete')
        return {
            'version': 1,
            'org_id': str(settings.org_id),
            'observed_at': (now or datetime.now(UTC)).isoformat(),
            'enabled': settings.enabled,
            'cycle_start_at': settings.cycle_start_at.isoformat(),
            'cycle_end_at': settings.cycle_end_at.isoformat(),
            'team_baseline': settings.cycle_start_spend,
            'default_member_budget_id': observation['default_member_budget_id'],
            'member_baselines': {
                u: settings.user_cycle_start_spend[u] for u in members
            },
            'counter_identities': {
                u: deepcopy(observation['member_counters'][u]) for u in members
            },
            'preserved_policy': deepcopy(observation['control_policy']),
        }

    def _set_targets(
        self, org_id: UUID, plan: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        current = plan['current_allowances']
        target = (
            None if not plan['enabled'] else plan['team_baseline'] + current['team']
        )
        targets = {}
        for user_id, baseline in plan['member_baselines'].items():
            allowance = current['members'].get(user_id, current['default_member'])
            targets[user_id] = (
                None
                if not plan['enabled'] or allowance is None
                else baseline + allowance
            )
        plan['expected_team_cap'] = target
        plan['expected_member_caps'] = targets
        plan['writes'] = budget_writes(
            org_id, observation['team_max_budget'], target, targets
        )
