"""Pure planning for an explicit takeover; no database or LiteLLM effects."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from storage.budget_control import BudgetControlConflict, budget_request_hash

Allowance = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
PositiveAllowance = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]


class BudgetAdoptionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    preview_fingerprint: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    current_team_allowance: Allowance
    current_default_member_allowance: Allowance | None
    current_member_allowances: dict[str, Allowance | None] = Field(default_factory=dict)
    future_monthly_limit: PositiveAllowance
    future_default_member_limit: PositiveAllowance | None
    future_member_limits: dict[str, PositiveAllowance | None] = Field(
        default_factory=dict
    )
    reset_day: Literal[1, 15]
    replace_native_reset_schedules: bool


class BudgetAdoptionUnsupported(ValueError):
    """The observation cannot safely define a takeover plan."""


def _counter(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        raise BudgetAdoptionUnsupported(f'{label} must be a known non-negative counter')
    return float(value)


def validate_adoption_observation(
    observation: dict[str, Any], member_ids: set[str]
) -> None:
    if observation.get('team_reset_known') is not True:
        raise BudgetAdoptionUnsupported(
            'The LiteLLM team reset schedule could not be verified'
        )
    if not isinstance(observation.get('control_policy'), dict):
        raise BudgetAdoptionUnsupported(
            'A complete LiteLLM policy observation is required'
        )
    counters = observation.get('member_counters')
    if not isinstance(counters, dict) or not member_ids <= counters.keys():
        raise BudgetAdoptionUnsupported(
            'OpenHands members are missing from LiteLLM; repair membership first'
        )
    _counter(observation.get('team_spend'), 'Team spend')
    for user_id in member_ids:
        counter = counters[user_id]
        if counter.get('source') not in {'membership', 'new_membership'}:
            raise BudgetAdoptionUnsupported(
                f'Member {user_id} has no verified enforcement counter'
            )
        if counter.get('reset_known') is not True:
            raise BudgetAdoptionUnsupported(
                f'Member {user_id} has an unknown reset schedule'
            )
        spend = _counter(counter.get('spend'), f'Member {user_id} spend')
        if counter['source'] == 'new_membership' and spend != 0:
            raise BudgetAdoptionUnsupported(
                'A newly created membership must start at zero'
            )
    if observation.get('default_member_budget_id') is not None:
        default = observation.get('default_member_budget')
        if (
            not isinstance(default, dict)
            or not {'budget_duration', 'budget_reset_at', 'max_budget'}
            <= default.keys()
        ):
            raise BudgetAdoptionUnsupported(
                'The default member budget could not be verified'
            )


def adoption_fingerprint(
    org_id: UUID,
    generation: int,
    member_ids: set[str],
    observation: dict[str, Any],
) -> str:
    validate_adoption_observation(observation, member_ids)
    return budget_request_hash(
        {
            'org_id': str(org_id),
            'generation': generation,
            'org_members': sorted(member_ids),
            'policy': {
                **observation['control_policy'],
                'keys': stable_key_policy(observation['control_policy']['keys']),
            },
            'team_reset': [
                observation['team_budget_duration'],
                observation['team_budget_reset_at'],
            ],
            'default_member_budget_id': observation['default_member_budget_id'],
            'counter_identities': {
                user_id: {
                    key: value for key, value in counter.items() if key != 'spend'
                }
                for user_id, counter in observation['member_counters'].items()
            },
        }
    )


def stable_key_policy(keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Independent key reset clocks can advance without changing the confirmed policy.
    return [{k: v for k, v in key.items() if k != 'budget_reset_at'} for key in keys]


def build_adoption_plan(
    org_id: UUID,
    generation: int,
    member_ids: set[str],
    observation: dict[str, Any],
    request: BudgetAdoptionRequest,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    fingerprint = adoption_fingerprint(org_id, generation, member_ids, observation)
    if fingerprint != request.preview_fingerprint:
        raise BudgetControlConflict(
            'Budget policies or membership changed; review a new preview'
        )
    unknown_overrides = (
        request.current_member_allowances.keys() | request.future_member_limits.keys()
    ) - member_ids
    if unknown_overrides:
        raise BudgetAdoptionUnsupported(
            'Allowances contain users outside the organization'
        )
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('Adoption time must be timezone-aware')
    counters = observation['member_counters']
    native_schedules = [
        observation.get('team_budget_duration'),
        observation.get('team_budget_reset_at'),
        (observation.get('default_member_budget') or {}).get('budget_duration'),
        (observation.get('default_member_budget') or {}).get('budget_reset_at'),
        *[
            counters[user_id].get(field)
            for user_id in member_ids
            for field in ('budget_duration', 'budget_reset_at')
        ],
    ]
    if (
        any(value is not None for value in native_schedules)
        and not request.replace_native_reset_schedules
    ):
        raise BudgetAdoptionUnsupported(
            'Confirm replacing native reset schedules before adopting'
        )
    baselines = {
        user_id: float(counters[user_id]['spend']) for user_id in sorted(member_ids)
    }
    team_baseline = float(observation['team_spend'])
    team_target = _counter(
        team_baseline + request.current_team_allowance, 'Team target'
    )
    validate_team_target(observation, team_target)
    member_targets = {}
    for user_id in sorted(member_ids):
        remaining = request.current_member_allowances.get(
            user_id, request.current_default_member_allowance
        )
        target = (
            None
            if remaining is None
            else _counter(baselines[user_id] + remaining, f'Member {user_id} target')
        )
        member_targets[user_id] = target
    team_block = plan_team_block(
        observation, exhausted=request.current_team_allowance == 0
    )
    writes = budget_writes(
        org_id,
        observation['team_max_budget'],
        team_target,
        member_targets,
        team_block=team_block,
    )
    return {
        'version': 1,
        'org_id': str(org_id),
        'preview_fingerprint': fingerprint,
        'observed_at': now.isoformat(),
        'cycle_start_at': now.isoformat(),
        'cycle_end_at': next_budget_reset(now, request.reset_day).isoformat(),
        'team_baseline': team_baseline,
        'default_member_budget_id': observation['default_member_budget_id'],
        'member_baselines': baselines,
        'counter_identities': {
            user_id: counters[user_id] for user_id in sorted(member_ids)
        },
        'current_allowances': {
            'team': request.current_team_allowance,
            'default_member': request.current_default_member_allowance,
            'members': request.current_member_allowances,
        },
        'future_policy': {
            'monthly_limit': request.future_monthly_limit,
            'default_user_monthly_limit': request.future_default_member_limit,
            'member_limits': request.future_member_limits,
            'reset_day': request.reset_day,
        },
        'expected_team_cap': team_target,
        'expected_member_caps': member_targets,
        'team_block': team_block,
        'preserved_policy': observation['control_policy'],
        'writes': writes,
    }


def validate_team_target(observation: dict[str, Any], target: float | None) -> None:
    soft_budget = observation['control_policy']['team'].get('soft_budget')
    if target is not None and soft_budget is not None:
        threshold = _counter(soft_budget, 'Team soft budget')
        if target <= threshold:
            raise BudgetAdoptionUnsupported(
                'The team cap must exceed the existing LiteLLM soft-budget alert threshold; '
                'review the threshold before changing budget policy'
            )


def plan_team_block(
    observation: dict[str, Any],
    *,
    exhausted: bool,
    previously_owned: bool = False,
) -> dict[str, Any]:
    initial = observation['control_policy']['team'].get('blocked')
    if initial is not None and not isinstance(initial, bool):
        raise BudgetAdoptionUnsupported('Team block state must be a boolean')
    if previously_owned and initial is not True:
        raise BudgetControlConflict('The OpenHands-owned team block was changed')
    if exhausted and initial is None:
        raise BudgetAdoptionUnsupported(
            'A known team block state is required for zero remaining allowance'
        )
    # LiteLLM denies team spending only above the cap, not at equality. Record
    # ownership so renewal can unblock only a block imposed by this controller.
    return {
        'initial': initial,
        'target': True if exhausted else False if previously_owned else initial,
        'budget_owned': exhausted and (initial is False or previously_owned),
    }


def budget_writes(
    org_id: UUID,
    old_team_cap: float | None,
    team_target: float | None,
    member_targets: dict[str, float | None],
    *,
    team_block: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    writes = []
    for user_id, target in sorted(member_targets.items()):
        writes.append(
            {
                'path': '/team/member_update',
                'body': {
                    'team_id': str(org_id),
                    'user_id': user_id,
                    'max_budget_in_team': target,
                    'budget_duration': None,
                },
            }
        )
    team_write = {
        'path': '/team/update',
        'body': {
            'team_id': str(org_id),
            'max_budget': team_target,
            'budget_duration': None,
            'budget_reset_at': None,
            'team_member_budget': None,
            'team_member_budget_duration': None,
        },
    }
    # Tighten the aggregate cap first; raise it only after applying member limits.
    if team_target is not None and (old_team_cap is None or team_target < old_team_cap):
        writes.insert(0, team_write)
    else:
        writes.append(team_write)
    if team_block is not None and team_block['initial'] is not team_block['target']:
        block_write = {
            'path': '/team/update',
            'body': {'team_id': str(org_id), 'blocked': team_block['target']},
        }
        if team_block['target'] is True:
            writes.insert(0, block_write)
        else:
            # Never reopen access until all the new caps are in place.
            writes.append(block_write)
    return writes


def next_budget_reset(now: datetime, reset_day: int) -> datetime:
    if reset_day not in (1, 15):
        raise BudgetAdoptionUnsupported('reset_day must be 1 or 15')
    next_reset = datetime(now.year, now.month, reset_day, tzinfo=UTC)
    if next_reset <= now:
        next_reset = datetime(
            now.year + (now.month == 12),
            now.month % 12 + 1,
            reset_day,
            tzinfo=UTC,
        )
    return next_reset
