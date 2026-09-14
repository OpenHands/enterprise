"""Journaled policy edits and renewals for an explicitly adopted organization."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from server.services.budget_adoption_plan import (
    Allowance,
    BudgetAdoptionUnsupported,
    PositiveAllowance,
    budget_writes,
    next_budget_reset,
    plan_team_block,
    validate_adoption_observation,
    validate_team_target,
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
from storage.org_member import OrgMember


class ManagedBudgetUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_generation: int = Field(ge=0)
    enabled: bool
    current_cycle_team_allowance: Allowance
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
    async def remove_member(
        self, org_id: UUID, user_id: UUID, actor: str
    ) -> dict[str, Any]:
        async with budget_control_session(self.engine, org_id) as control:
            settings = await self._settings(control)
            previous = await self._verified_operation(control)
            member = await control.session.get(
                OrgMember, {'org_id': org_id, 'user_id': user_id}
            )
            if member is None:
                raise BudgetControlConflict('Organization membership no longer exists')
            members = set(previous.plan['member_baselines'])
            if str(user_id) not in members:
                raise BudgetControlConflict('Finish member admission before removal')
            observation = await self._observe(org_id)
            self._check_managed_observation(previous, observation, members)
            plan = self._membership_plan(
                settings, observation, previous, set(), datetime.now(UTC)
            )
            plan['inactive_member_ids'] = sorted(
                set(plan['inactive_member_ids']) | {str(user_id)}
            )
            plan['revoked_member_ids'] = [str(user_id)]
            self._set_targets(org_id, plan, observation)
            plan['writes'] = [
                write
                for write in plan['writes']
                if write['path'] == '/team/member_update'
                and write['body']['user_id'] == str(user_id)
            ]
            key = f'remove:{settings.control_generation}:{user_id}'
            # Local access removal and the recovery operation become durable together.
            await control.session.delete(member)
            operation = await control.reserve_operation(
                idempotency_key=key,
                request_hash=budget_request_hash({'key': key}),
                kind='repair',
                actor=actor,
                plan=plan,
            )
            await self._execute(control, operation)
            if operation.status == 'applied':
                await self._after_maintenance(control, operation.verification)
            return await self._result(control, operation)

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
                members = set(previous.plan['member_baselines'])
                if (
                    request.current_cycle_member_allowances.keys()
                    | request.future_member_limits.keys()
                ) - members:
                    raise BudgetAdoptionUnsupported(
                        'Allowances contain users outside the organization'
                    )
                observation = await self._observe(org_id)
                self._check_managed_observation(previous, observation, members)
                plan = self._plan(settings, observation, members, previous)
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
            previous_members = set(previous.plan['member_baselines'])
            inactive = set(previous.plan.get('inactive_member_ids', []))
            if previous_members - inactive - members:
                raise BudgetControlConflict(
                    'Membership was removed; explicit lifecycle reconciliation is required'
                )
            added_members = members - (previous_members - inactive)
            if added_members:
                from storage.lite_llm_manager import LiteLlmManager

                for user_id in sorted(added_members - previous_members):
                    await LiteLlmManager.prepare_new_managed_member(
                        user_id, str(org_id)
                    )
            observation = await self._observe(org_id)
            self._check_managed_observation(previous, observation, previous_members)
            if added_members:
                validate_adoption_observation(observation, members)
                for user_id in added_members:
                    counter = observation['member_counters'][user_id]
                    if any(
                        counter.get(field) is not None
                        for field in ('budget_duration', 'budget_reset_at')
                    ):
                        raise BudgetWriteDenied(
                            'New member has an independent native reset schedule'
                        )
            if not settings.enabled or now < cycle_end:
                if added_members:
                    plan = self._membership_plan(
                        settings, observation, previous, added_members, now
                    )
                    self._set_targets(org_id, plan, observation)
                    key = f'members:{settings.control_generation}:{budget_request_hash({"added": sorted(added_members)})}'
                    operation = await control.reserve_operation(
                        idempotency_key=key,
                        request_hash=budget_request_hash({'key': key}),
                        kind='repair',
                        actor='budget-maintenance',
                        plan=plan,
                    )
                    await self._execute(control, operation)
                    if operation.status == 'applied':
                        await self._after_maintenance(control, operation.verification)
                    result = await self._result(control, operation)
                    result['cycle_rolled'] = False
                    result['members_reconciled'] = (
                        sorted(added_members) if operation.status == 'applied' else []
                    )
                    return result
                await self._after_maintenance(control, observation)
                return {
                    'status': 'healthy',
                    'control_mode': 'managed',
                    'cycle_rolled': False,
                }

            plan = self._membership_plan(
                settings, observation, previous, added_members, now
            )
            future = deepcopy(previous.plan['future_policy'])
            # Renew once at the observation; never invent historical counter boundaries.
            plan['previous_cycle_end_at'] = cycle_end.isoformat()
            plan['cycle_start_at'] = now.isoformat()
            plan['cycle_end_at'] = next_budget_reset(
                now, settings.reset_day
            ).isoformat()
            plan['team_baseline'] = observation['team_spend']
            plan['member_baselines'] = {
                u: observation['member_counters'][u]['spend']
                for u in plan['member_baselines']
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

    def _membership_plan(
        self,
        settings: OrgBudgetSettings,
        observation: dict[str, Any],
        previous: OrgBudgetOperation,
        added_members: set[str],
        now: datetime,
    ) -> dict[str, Any]:
        plan = self._plan(
            settings,
            observation,
            set(previous.plan['member_baselines']),
            previous,
            now=now,
        )
        plan['current_allowances'] = deepcopy(previous.plan['current_allowances'])
        plan['future_policy'] = deepcopy(previous.plan['future_policy'])
        plan['added_member_ids'] = sorted(added_members)
        inactive = set(previous.plan.get('inactive_member_ids', []))
        plan['inactive_member_ids'] = sorted(inactive - added_members)
        for user_id in added_members:
            counter = observation['member_counters'][user_id]
            if user_id not in plan['member_baselines']:
                plan['member_baselines'][user_id] = counter['spend']
            if user_id in inactive:
                plan['member_credential_epochs'][user_id] = uuid4().hex
            plan['counter_identities'][user_id] = deepcopy(counter)
        return plan

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
        future = plan['future_policy']
        overrides = await OrgBudgetStore(control.session).get_overrides(control.org_id)
        actual_overrides = sorted(
            [(str(o.user_id), o.monthly_limit, o.is_disabled) for o in overrides],
            key=lambda row: row[0],
        )
        expected_overrides = sorted(
            (user_id, limit, limit is None)
            for user_id, limit in future['member_limits'].items()
        )
        if (
            settings.enabled != plan.get('enabled', True)
            or settings.monthly_limit != future['monthly_limit']
            or settings.default_user_monthly_limit
            != future['default_user_monthly_limit']
            or settings.reset_day != future['reset_day']
            or actual_overrides != expected_overrides
        ):
            raise BudgetWriteDenied(
                'Future policy or enabled state differs from its verified operation'
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
        previous: OrgBudgetOperation,
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
            'team_block': deepcopy(previous.plan.get('team_block', {})),
            'inactive_member_ids': list(previous.plan.get('inactive_member_ids', [])),
            'member_credential_epochs': deepcopy(
                previous.plan.get('member_credential_epochs', {})
            ),
        }

    def _set_targets(
        self, org_id: UUID, plan: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        current = plan['current_allowances']
        target = (
            None if not plan['enabled'] else plan['team_baseline'] + current['team']
        )
        validate_team_target(observation, target)
        targets = {}
        for user_id, baseline in plan['member_baselines'].items():
            allowance = current['members'].get(user_id, current['default_member'])
            targets[user_id] = (
                0.0
                if user_id in plan.get('inactive_member_ids', [])
                else (
                    None
                    if not plan['enabled'] or allowance is None
                    else baseline + allowance
                )
            )
        plan['expected_team_cap'] = target
        plan['expected_member_caps'] = targets
        plan['team_block'] = plan_team_block(
            observation,
            exhausted=plan['enabled'] and current['team'] == 0,
            previously_owned=plan['team_block'].get('budget_owned', False),
        )
        plan['writes'] = budget_writes(
            org_id,
            observation['team_max_budget'],
            target,
            targets,
            team_block=plan['team_block'],
        )
