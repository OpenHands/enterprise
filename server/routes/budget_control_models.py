"""Public budget-control contracts; never return native records or credentials."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BudgetControlState(BaseModel):
    control_mode: Literal['managed', 'external', 'needs_adoption']
    generation: int


class BudgetHandoffRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    expected_generation: int = Field(ge=0, strict=True)


class BudgetCurrentAllowances(BaseModel):
    team: float | None
    default_member: float | None
    members: dict[str, float | None]


class BudgetFuturePolicy(BaseModel):
    monthly_limit: float
    default_user_monthly_limit: float | None
    member_limits: dict[str, float | None]
    reset_day: Literal[1, 15]


class BudgetTeamBlock(BaseModel):
    initial: bool | None
    target: bool | None
    budget_owned: bool


class BudgetOperationResponse(BudgetControlState):
    operation_id: UUID
    operation_generation: int
    kind: Literal['adopt', 'settings', 'rollover', 'repair']
    status: Literal['pending', 'applied', 'abandoned']
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    current_allowances: BudgetCurrentAllowances
    future_policy: BudgetFuturePolicy
    cycle_end_at: datetime
    team_block: BudgetTeamBlock | None

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> 'BudgetOperationResponse':
        return cls.model_validate(
            {
                **result,
                'error': (
                    'The requested limits are not yet verified. Some changes may have '
                    'applied. Retry this operation to finish without granting extra allowance.'
                    if result.get('error')
                    else None
                ),
            }
        )


class NativeBudgetPolicy(BaseModel):
    max_budget: float | None = None
    soft_budget: float | None = None
    budget_duration: str | None = None
    budget_reset_at: str | None = None
    models: list[str] | None = None
    allowed_models: list[str] | None = None
    blocked: bool | None = None
    rpm_limit: float | None = None
    tpm_limit: float | None = None
    max_parallel_requests: int | None = None
    has_additional_budget_windows: bool = False
    has_model_budgets: bool = False

    @classmethod
    def from_native(cls, policy: dict[str, Any]) -> 'NativeBudgetPolicy':
        return cls.model_validate(
            {
                **policy,
                'has_additional_budget_windows': bool(policy.get('budget_limits')),
                'has_model_budgets': bool(policy.get('model_max_budget')),
            }
        )


class BudgetPreviewMember(BaseModel):
    user_id: str
    in_organization: bool
    lifetime_spend: float
    enforcement_spend: float
    max_budget: float | None
    counter_source: Literal['membership', 'new_membership']
    budget_source: Literal['private_member', 'default_member', 'team']
    budget_duration: str | None
    budget_reset_at: str | None
    policy: NativeBudgetPolicy


class BudgetPreviewKey(BaseModel):
    user_id: str | None
    policy: NativeBudgetPolicy


class BudgetPreviewResponse(BudgetControlState):
    fingerprint: str
    observed_at: datetime
    pending_operation_id: UUID | None
    team_spend: float
    team_policy: NativeBudgetPolicy
    default_member_policy: NativeBudgetPolicy
    members: list[BudgetPreviewMember]
    keys: list[BudgetPreviewKey]

    @classmethod
    def from_preview(cls, preview: dict[str, Any]) -> 'BudgetPreviewResponse':
        observation = preview['observation']
        policy = observation['control_policy']
        return cls(
            control_mode=preview['control_mode'],
            generation=preview['generation'],
            fingerprint=preview['fingerprint'],
            observed_at=preview['observed_at'],
            pending_operation_id=preview['pending_operation_id'],
            team_spend=observation['team_spend'],
            team_policy=NativeBudgetPolicy.from_native(policy['team']),
            default_member_policy=NativeBudgetPolicy.from_native(
                policy['default_member']
            ),
            members=[
                BudgetPreviewMember(
                    user_id=user_id,
                    in_organization=user_id in preview['org_member_ids'],
                    lifetime_spend=observation['members'][user_id]['spend'],
                    enforcement_spend=counter['spend'],
                    max_budget=observation['members'][user_id]['max_budget'],
                    counter_source=counter['source'],
                    budget_source=counter['budget_source'],
                    budget_duration=counter['budget_duration'],
                    budget_reset_at=counter['budget_reset_at'],
                    policy=NativeBudgetPolicy.from_native(
                        policy['members'].get(user_id, {})
                    ),
                )
                for user_id, counter in sorted(observation['member_counters'].items())
            ],
            keys=[
                BudgetPreviewKey(
                    user_id=key.get('user_id'),
                    policy=NativeBudgetPolicy.from_native(key),
                )
                for key in policy['keys']
            ],
        )
