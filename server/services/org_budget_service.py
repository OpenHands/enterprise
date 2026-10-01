from __future__ import annotations

import asyncio
import calendar
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import AsyncGenerator, Literal
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy import and_, inspect, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.services.injector import Injector, InjectorState
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.app_server.utils.slack_config import is_slack_configured
from server.auth.authorization import RoleName
from server.routes.org_models import OrgBudgetSettingsUpdate, SpendStatus
from server.services.smtp_email_service import SMTPEmailService
from storage.database import sqlstate
from storage.lite_llm_manager import LiteLlmManager
from storage.org import Org
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.org_budget_utils import budget_values_match as _budget_values_match
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from storage.slack_team import SlackTeam
from storage.user import User
from utils.sql import escape_ilike

# The Quint oracle client is vendored under quint-specs/, which the application
# image does not ship. Without it the instrumentation below is a no-op, so the
# app must not depend on it being importable.
try:
    import quint_oracle
except ModuleNotFoundError:  # pragma: no cover
    from types import SimpleNamespace

    quint_oracle = SimpleNamespace(
        log=lambda *args, **kwargs: None,
        In=lambda value, domain: value,
    )


try:
    from slack_sdk.web.async_client import AsyncWebClient

    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False


DEFAULT_THRESHOLDS = (
    (80, True, False),
    (90, True, True),
    (100, True, True),
)

LITELLM_FINANCIAL_READ_MAX_ATTEMPTS = 3
LITELLM_FINANCIAL_READ_RETRY_DELAY_SECONDS = 0.1

# Postgres SQLSTATE for unique_violation.
_UNIQUE_VIOLATION = '23505'


class BudgetChangeRejectedError(Exception):
    def __init__(self, previous_policy_verified: bool = False):
        super().__init__()
        self.previous_policy_verified = previous_policy_verified

    @property
    def detail(self) -> dict:
        message = "Budget change wasn't saved. "
        if self.previous_policy_verified:
            message += 'Your previous limits remain in effect. Please retry.'
        else:
            message += (
                'Previous settings are unchanged, but their enforcement could not '
                'be verified. Please retry.'
            )
        return {
            'code': 'budget_change_rejected',
            'message': message,
            'previous_policy_verified': self.previous_policy_verified,
        }


@dataclass
class BudgetCycle:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class LiteLlmMemberFinancialSnapshot:
    spend: float
    max_budget: float | None
    uses_shared_budget: bool


@dataclass(frozen=True)
class LiteLlmFinancialSnapshot:
    team_spend: float
    team_max_budget: float | None
    members: dict[str, LiteLlmMemberFinancialSnapshot]
    observed_at: datetime


@dataclass(frozen=True)
class BudgetFinancialSnapshotResult:
    snapshot: LiteLlmFinancialSnapshot | None
    status: SpendStatus
    error: str | None = None


BudgetReconciliationState = Literal[
    'inactive', 'pending', 'healthy', 'degraded', 'failed'
]


def _add_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _subtract_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _cycle_day(year: int, month: int, reset_day: int) -> int:
    """The reset day this month can actually hold.

    reset_day is a plain Integer column with no CHECK constraint, so it is untrusted:
    clamp both ends to keep every value a day datetime() accepts.
    """
    return max(1, min(reset_day, calendar.monthrange(year, month)[1]))


def _current_cycle_start(now: datetime, reset_day: int) -> datetime:
    day_this_month = _cycle_day(now.year, now.month, reset_day)
    if now.day >= day_this_month:
        return datetime(now.year, now.month, day_this_month, tzinfo=UTC)
    prev_year, prev_month = _subtract_month(now.year, now.month)
    return datetime(
        prev_year, prev_month, _cycle_day(prev_year, prev_month, reset_day), tzinfo=UTC
    )


def _next_cycle_start(cycle_start: datetime, reset_day: int) -> datetime:
    year, month = _add_month(cycle_start.year, cycle_start.month)
    return datetime(year, month, _cycle_day(year, month, reset_day), tzinfo=UTC)


def _optional_nonnegative_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field_name} must be a number or null')
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f'{field_name} must be a finite non-negative number')
    return result


def _required_nonnegative_float(value: object, field_name: str) -> float:
    result = _optional_nonnegative_float(value, field_name)
    if result is None:
        raise ValueError(f'{field_name} is required')
    return result


def _is_retryable_litellm_read_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, httpx.TransportError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


def _parse_litellm_financial_snapshot(
    financial_data: object,
    observed_at: datetime | None = None,
) -> LiteLlmFinancialSnapshot:
    if not isinstance(financial_data, dict):
        raise ValueError('response must be an object')
    if 'team_max_budget' not in financial_data:
        raise ValueError('team_max_budget is required')

    members_data = financial_data.get('members')
    if not isinstance(members_data, dict):
        raise ValueError('members must be an object')

    members: dict[str, LiteLlmMemberFinancialSnapshot] = {}
    for user_id, member_data in members_data.items():
        if not isinstance(user_id, str) or not user_id:
            raise ValueError('member id must be a non-empty string')
        if not isinstance(member_data, dict):
            raise ValueError(f'members.{user_id} must be an object')
        if 'max_budget' not in member_data:
            raise ValueError(f'members.{user_id}.max_budget is required')
        uses_shared_budget = member_data.get('uses_shared_budget')
        if not isinstance(uses_shared_budget, bool):
            raise ValueError(f'members.{user_id}.uses_shared_budget must be a boolean')
        members[user_id] = LiteLlmMemberFinancialSnapshot(
            spend=_required_nonnegative_float(
                member_data.get('spend'), f'members.{user_id}.spend'
            ),
            max_budget=_optional_nonnegative_float(
                member_data.get('max_budget'), f'members.{user_id}.max_budget'
            ),
            uses_shared_budget=uses_shared_budget,
        )

    return LiteLlmFinancialSnapshot(
        team_spend=_required_nonnegative_float(
            financial_data.get('team_spend'), 'team_spend'
        ),
        team_max_budget=_optional_nonnegative_float(
            financial_data.get('team_max_budget'), 'team_max_budget'
        ),
        members=members,
        observed_at=observed_at or datetime.now(UTC),
    )


def _litellm_cycle_spend(
    settings: OrgBudgetSettings,
    snapshot: LiteLlmFinancialSnapshot | None,
) -> float | None:
    if snapshot is None:
        return None
    return max(snapshot.team_spend - settings.cycle_start_spend, 0.0)


def _litellm_member_cycle_spend(
    settings: OrgBudgetSettings,
    user_id: str,
    member_info: LiteLlmMemberFinancialSnapshot | None,
) -> float | None:
    if member_info is None:
        return None
    baseline = (settings.user_cycle_start_spend or {}).get(user_id)
    if baseline is None:
        return None
    return max(member_info.spend - baseline, 0.0)


def _litellm_unmapped_cycle_spend(
    settings: OrgBudgetSettings,
    snapshot: LiteLlmFinancialSnapshot | None,
    org_member_ids: set[str],
) -> tuple[float | None, int | None]:
    if snapshot is None:
        return None, None

    unmanaged_member_ids = set(snapshot.members) - org_member_ids
    if not unmanaged_member_ids:
        return 0.0, 0

    baselines = settings.user_cycle_start_spend or {}
    if any(user_id not in baselines for user_id in unmanaged_member_ids):
        return None, len(unmanaged_member_ids)

    return (
        sum(
            max(snapshot.members[user_id].spend - baselines[user_id], 0.0)
            for user_id in unmanaged_member_ids
        ),
        len(unmanaged_member_ids),
    )


def _effective_user_budget_limit(
    override: OrgUserBudgetOverride | None,
    default_limit: float | None,
) -> tuple[float | None, bool, bool]:
    if override:
        if override.is_disabled:
            return None, True, True
        return override.monthly_limit, False, True
    return default_limit, False, False


def _member_cap(baseline: float, effective_limit: float) -> float:
    # LiteLLM compares cumulative spend against an absolute member cap, so a cap
    # below the member's cycle baseline is already exceeded the moment it is
    # written. Nothing rejects a non-positive allowance, so clamp it here.
    return baseline + max(effective_limit, 0)


def _budget_sync_readback_errors(
    snapshot: LiteLlmFinancialSnapshot,
    expected_team_budget: float | None,
    expected_member_budgets: dict[str, float | None],
) -> list[str]:
    errors: list[str] = []
    actual_team_budget = snapshot.team_max_budget
    if not _budget_values_match(actual_team_budget, expected_team_budget):
        errors.append(
            'team_budget_mismatch: '
            f'expected={expected_team_budget} actual={actual_team_budget}'
        )

    for user_id, expected_budget in expected_member_budgets.items():
        member = snapshot.members.get(user_id)
        if member is None:
            errors.append(f'member_budget_missing: {user_id}')
            continue

        actual_budget = member.max_budget
        uses_shared_budget = member.uses_shared_budget
        if expected_budget is None:
            if not uses_shared_budget:
                errors.append(
                    f'member_budget_mismatch: {user_id}: '
                    f'expected=shared actual={actual_budget}'
                )
        elif uses_shared_budget or not _budget_values_match(
            actual_budget, expected_budget
        ):
            errors.append(
                f'member_budget_mismatch: {user_id}: '
                f'expected={expected_budget} actual={actual_budget} '
                f'uses_shared_budget={uses_shared_budget}'
            )
    return errors


def _desired_team_budget(settings: OrgBudgetSettings) -> float | None:
    if settings.enabled and settings.monthly_limit:
        return settings.cycle_start_spend + settings.monthly_limit
    return None


def _budget_policy_comparison(
    settings: OrgBudgetSettings,
    overrides: list[OrgUserBudgetOverride],
    org_member_ids: set[str],
    snapshot_result: BudgetFinancialSnapshotResult,
) -> dict:
    """Compare desired Enterprise policy with a fresh LiteLLM readback."""
    desired_team_budget = _desired_team_budget(settings)
    snapshot = snapshot_result.snapshot if snapshot_result.status == 'live' else None
    applied_team_budget = snapshot.team_max_budget if snapshot is not None else None

    policy_matches: bool | None = None
    drift_errors: list[str] = []
    if snapshot is not None:
        override_map = {str(override.user_id): override for override in overrides}
        baselines = settings.user_cycle_start_spend or {}
        expected_member_budgets: dict[str, float | None] = {}
        for user_id in sorted(org_member_ids):
            if user_id not in snapshot.members:
                drift_errors.append(f'member_missing_from_litellm: {user_id}')
                continue
            effective_limit, is_disabled, _ = _effective_user_budget_limit(
                override_map.get(user_id), settings.default_user_monthly_limit
            )
            if not is_disabled and effective_limit is not None:
                baseline = baselines.get(user_id)
                if baseline is None:
                    drift_errors.append(f'member_cycle_baseline_missing: {user_id}')
                    continue
                expected_member_budgets[user_id] = _member_cap(
                    baseline, effective_limit
                )
            else:
                expected_member_budgets[user_id] = None

        drift_errors.extend(
            _budget_sync_readback_errors(
                snapshot,
                desired_team_budget,
                expected_member_budgets,
            )
        )
        policy_matches = not drift_errors

    sync_status = settings.litellm_last_sync_status
    if snapshot is None:
        if sync_status == 'error':
            reconciliation_state: BudgetReconciliationState = 'failed'
        elif settings.enabled:
            reconciliation_state = 'pending' if sync_status is None else 'degraded'
        else:
            reconciliation_state = 'inactive'
    elif policy_matches is False or sync_status == 'error':
        reconciliation_state = 'degraded'
    elif settings.enabled:
        reconciliation_state = 'healthy' if sync_status == 'success' else 'pending'
    else:
        reconciliation_state = 'inactive'

    reconciliation_error = settings.litellm_last_sync_error
    if reconciliation_error is None and drift_errors:
        reconciliation_error = drift_errors[0]
        if len(drift_errors) > 1:
            reconciliation_error += f' (+{len(drift_errors) - 1} more)'
    if reconciliation_error is None and snapshot is None and settings.enabled:
        reconciliation_error = snapshot_result.error

    return {
        'reconciliation_state': reconciliation_state,
        'reconciliation_error': reconciliation_error,
        'desired_team_max_budget': desired_team_budget,
        'applied_team_max_budget': applied_team_budget,
        'budget_policy_matches': policy_matches,
        'applied_at': (
            settings.litellm_last_sync_at
            if policy_matches is True and sync_status == 'success'
            else None
        ),
        'applied_policy_observed_at': (
            snapshot.observed_at if snapshot is not None else None
        ),
    }


class OrgBudgetService:
    def __init__(
        self,
        db_session: AsyncSession | None = None,
        store: OrgBudgetStore | None = None,
    ):
        if store is None:
            if db_session is None:
                raise ValueError('db_session is required when store is not provided')
            store = OrgBudgetStore(db_session=db_session)
        self.store = store
        self.db_session = store.db_session

    async def _is_personal_org(self, org_id: UUID) -> bool:
        result = await self.db_session.execute(select(User.id).where(User.id == org_id))
        return result.scalar_one_or_none() is not None

    async def _reject_personal_org(
        self, org_id: UUID, quint_action: str | None = None
    ) -> None:
        if await self._is_personal_org(org_id):
            if quint_action is not None:
                quint_oracle.log(
                    quint_action,
                    'org-budgets',
                    org_id=quint_oracle.In('personal', 'ORG_IDS'),
                    outcome='rejected',
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Organization budgets are not available for personal workspaces',
            )

    async def get_budget_state(
        self,
        org_id: UUID,
        users_page: int = 1,
        users_per_page: int = 50,
        users_search: str | None = None,
        users_status: str | None = None,
    ):
        await self._reject_personal_org(org_id, 'get_budget_state')
        settings = await self._get_settings_for_read(org_id)
        thresholds = await self._get_thresholds(org_id)
        if inspect(settings).transient:
            # Negative IDs identify defaults that have not been persisted yet.
            thresholds = [
                OrgBudgetThreshold(
                    id=-percentage,
                    org_id=org_id,
                    percentage=percentage,
                    email_enabled=email_enabled,
                    slack_enabled=slack_enabled,
                )
                for percentage, email_enabled, slack_enabled in DEFAULT_THRESHOLDS
            ]
        overrides = await self._get_overrides(org_id)
        cycle = self._current_cycle(settings)

        snapshot_result = await self._get_financial_snapshot(
            org_id, settings, allow_stale=True
        )
        current_spend = _litellm_cycle_spend(settings, snapshot_result.snapshot)
        org_member_ids = await self._org_member_ids(org_id)
        unmapped_spend, unmapped_member_count = _litellm_unmapped_cycle_spend(
            settings, snapshot_result.snapshot, org_member_ids
        )
        users, users_total = await self._build_user_budget_rows(
            org_id,
            settings,
            snapshot_result.snapshot,
            users_page=users_page,
            users_per_page=users_per_page,
            users_search=users_search,
            users_status=users_status,
        )
        quint_oracle.log(
            'get_budget_state',
            'org-budgets',
            org_id=quint_oracle.In('org', 'ORG_IDS'),
            spend_status=snapshot_result.status,
        )
        policy_comparison = _budget_policy_comparison(
            settings,
            overrides,
            org_member_ids,
            snapshot_result,
        )
        return {
            **await self._alert_availability(settings),
            'settings': settings,
            'thresholds': thresholds,
            'cycle': cycle,
            'current_spend': current_spend,
            'spend_status': snapshot_result.status,
            'spend_observed_at': (
                snapshot_result.snapshot.observed_at
                if snapshot_result.snapshot is not None
                else None
            ),
            'spend_error': snapshot_result.error,
            'unmapped_spend': unmapped_spend,
            'unmapped_member_count': unmapped_member_count,
            'users': users,
            'users_total': users_total,
            'users_page': users_page,
            'users_per_page': users_per_page,
            **policy_comparison,
        }

    async def run_budget_maintenance(self, org_id: UUID) -> dict:
        if await self._is_personal_org(org_id):
            quint_oracle.log(
                'run_budget_maintenance',
                'org-budgets',
                org_id=quint_oracle.In('personal', 'ORG_IDS'),
                cycle_rolled=False,
            )
            return {
                'cycle_start_at': None,
                'cycle_end_at': None,
                'cycle_rolled': False,
                'current_spend': 0.0,
                'skipped': 'personal_org',
            }

        settings = await self._get_or_create_settings(org_id, for_update=True)
        thresholds = await self._get_thresholds(org_id)
        overrides = await self._get_overrides(org_id)
        cycle = self._current_cycle(settings)

        snapshot_result = await self._get_financial_snapshot(
            org_id,
            settings,
            allow_stale=False,
        )
        if snapshot_result.snapshot is None:
            reconciliation_error = self._snapshot_unavailable_detail()
            if self._needs_litellm_sync(settings, overrides):
                await self._block_litellm_admission(org_id, settings)
            await self._record_litellm_sync(
                settings,
                'error',
                reconciliation_error,
            )
            quint_oracle.log(
                'run_budget_maintenance',
                'org-budgets',
                org_id=quint_oracle.In('org', 'ORG_IDS'),
                cycle_rolled=False,
            )
            return {
                'cycle_start_at': cycle.start_at,
                'cycle_end_at': cycle.end_at,
                'cycle_rolled': False,
                'current_spend': None,
                'skipped': 'litellm_spend_unavailable',
                'reconciliation_status': 'error',
                'reconciliation_error': reconciliation_error,
            }

        snapshot = snapshot_result.snapshot
        next_cycle = self._current_cycle(settings).end_at
        cycle_due = datetime.now(UTC) >= next_cycle
        policy_matches = False
        if cycle_due:
            if self._needs_litellm_sync(
                settings, overrides
            ) and not await self._block_litellm_admission(org_id, settings):
                return {
                    'cycle_start_at': cycle.start_at,
                    'cycle_end_at': cycle.end_at,
                    'cycle_rolled': False,
                    'current_spend': _litellm_cycle_spend(settings, snapshot),
                    'skipped': 'admission_block_failed',
                    'reconciliation_status': 'error',
                    'reconciliation_error': settings.litellm_last_sync_error,
                }
            repair_result = await self._repair_missing_members_for_cycle(
                org_id, settings, overrides, snapshot
            )
        else:
            policy_matches = await self._budget_policy_matches_snapshot(
                org_id, settings, overrides, snapshot
            )

        if cycle_due:
            if repair_result.snapshot is None:
                reconciliation_error = (
                    repair_result.error
                    or 'LiteLLM membership repair failed before cycle rollover.'
                )[:500]
                await self._record_litellm_sync(
                    settings,
                    'error',
                    reconciliation_error,
                )
                quint_oracle.log(
                    'run_budget_maintenance',
                    'org-budgets',
                    org_id=quint_oracle.In('org', 'ORG_IDS'),
                    cycle_rolled=False,
                )
                return {
                    'cycle_start_at': cycle.start_at,
                    'cycle_end_at': cycle.end_at,
                    'cycle_rolled': False,
                    'current_spend': _litellm_cycle_spend(settings, snapshot),
                    'skipped': 'litellm_membership_repair_failed',
                    'reconciliation_status': 'error',
                    'reconciliation_error': reconciliation_error,
                }
            snapshot = repair_result.snapshot

        cycle_rolled = await self._roll_cycle_if_needed(
            settings, thresholds, overrides, snapshot
        )
        cycle = self._current_cycle(settings)

        current_spend = _litellm_cycle_spend(settings, snapshot)
        assert current_spend is not None
        await self._maybe_send_alerts(
            org_id,
            settings,
            thresholds,
            current_spend,
            cycle.start_at,
        )
        if not cycle_rolled:
            if not self._needs_litellm_sync(settings, overrides):
                await self._record_litellm_sync(settings, 'skipped')
            elif policy_matches:
                admission_ready = settings.litellm_last_sync_status == 'success'
                if not admission_ready:
                    admission_ready = await self._restore_litellm_admission(
                        org_id, settings
                    )
                if admission_ready:
                    await self._record_litellm_sync(settings, 'success')
            else:
                await self._sync_litellm_budgets(
                    org_id, settings, overrides, snapshot=snapshot
                )

        quint_oracle.log(
            'run_budget_maintenance',
            'org-budgets',
            org_id=quint_oracle.In('org', 'ORG_IDS'),
            cycle_rolled=cycle_rolled,
        )
        return {
            'cycle_start_at': cycle.start_at,
            'cycle_end_at': cycle.end_at,
            'cycle_rolled': cycle_rolled,
            'current_spend': current_spend,
            'reconciliation_status': settings.litellm_last_sync_status,
            'reconciliation_error': settings.litellm_last_sync_error,
        }

    async def update_budget_settings(
        self,
        org_id: UUID,
        update_data,
        users_page: int = 1,
        users_per_page: int = 50,
        users_search: str | None = None,
        users_status: str | None = None,
    ):
        await self._reject_personal_org(org_id, 'update_budget_settings')
        settings = await self._get_or_create_settings(org_id, for_update=True)
        thresholds = await self._get_thresholds(org_id)
        overrides = await self._get_overrides(org_id)

        await self._validate_alert_settings(settings, update_data, thresholds)

        fields_set = update_data.model_fields_set
        previous_enabled = settings.enabled
        baseline_snapshot: LiteLlmFinancialSnapshot | None = None
        enabled = update_data.enabled if 'enabled' in fields_set else settings.enabled
        monthly_limit = (
            update_data.monthly_limit
            if 'monthly_limit' in fields_set
            else settings.monthly_limit
        )

        if enabled and (monthly_limit is None or monthly_limit <= 0):
            quint_oracle.log(
                'update_budget_settings',
                'org-budgets',
                org_id=quint_oracle.In('org', 'ORG_IDS'),
                outcome='rejected',
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='monthly_limit is required when budgets are enabled',
            )

        if not previous_enabled and enabled:
            snapshot_result = await self._get_financial_snapshot(
                org_id,
                settings,
                allow_stale=False,
                require_complete_membership=True,
            )
            if snapshot_result.snapshot is None:
                quint_oracle.log(
                    'update_budget_settings',
                    'org-budgets',
                    org_id=quint_oracle.In('org', 'ORG_IDS'),
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=self._snapshot_unavailable_detail(),
                )
            baseline_snapshot = snapshot_result.snapshot

        admission_ready = None
        unchanged_disabled_policy = False
        if (
            not previous_enabled
            and not enabled
            and settings.litellm_last_sync_status == 'success'
            and (
                'default_user_monthly_limit' not in fields_set
                or update_data.default_user_monthly_limit
                == settings.default_user_monthly_limit
            )
        ):
            baseline_snapshot = (
                await self._get_financial_snapshot(org_id, settings, allow_stale=False)
            ).snapshot
            unchanged_disabled_policy = (
                baseline_snapshot is not None
                and await self._budget_policy_matches_snapshot(
                    org_id, settings, overrides, baseline_snapshot
                )
            )
        if not unchanged_disabled_policy and (
            self._needs_litellm_sync(settings, overrides)
            or fields_set & {'enabled', 'default_user_monthly_limit'}
        ):
            admission_ready = await self._begin_budget_edit(org_id, settings, overrides)

        if 'enabled' in fields_set:
            settings.enabled = update_data.enabled
        if 'monthly_limit' in fields_set:
            settings.monthly_limit = update_data.monthly_limit
        if 'reset_day' in fields_set and update_data.reset_day != settings.reset_day:
            settings.reset_day = update_data.reset_day
            # Keep spend until the next future occurrence, even across maintenance.
            settings.next_reset_at = _next_cycle_start(
                _current_cycle_start(datetime.now(UTC), settings.reset_day),
                settings.reset_day,
            )
        if 'default_user_monthly_limit' in fields_set:
            settings.default_user_monthly_limit = update_data.default_user_monthly_limit
        if 'slack_channel' in fields_set:
            settings.slack_channel = update_data.slack_channel
        if 'slack_team_id' in fields_set:
            settings.slack_team_id = update_data.slack_team_id

        if not previous_enabled and settings.enabled:
            assert baseline_snapshot is not None
            settings.cycle_start_at = _current_cycle_start(
                datetime.now(UTC), settings.reset_day
            )
            settings.next_reset_at = None
            settings.cycle_start_spend = baseline_snapshot.team_spend
            settings.user_cycle_start_spend = {
                user_id: member.spend
                for user_id, member in baseline_snapshot.members.items()
            }
            # An explicit admin re-baseline: replace any row for this cycle.
            await self.store.record_cycle_baselines(
                org_id,
                settings.cycle_start_at,
                settings.user_cycle_start_spend,
                source=OrgBudgetCycleBaseline.SOURCE_ENABLEMENT,
                observed_at=baseline_snapshot.observed_at,
                replace=True,
            )
            settings.litellm_known_member_ids = sorted(
                await self._org_member_ids(org_id)
            )

        if 'thresholds' in fields_set and update_data.thresholds is not None:
            await self._replace_thresholds(org_id, thresholds, update_data.thresholds)
            thresholds = await self._get_thresholds(org_id)

        await self.store.flush()
        await self.store.refresh(settings)

        snapshot = baseline_snapshot
        if admission_ready is not False:
            snapshot = await self._sync_litellm_budgets(
                org_id,
                settings,
                overrides,
                clear_disabled=bool(
                    fields_set & {'enabled', 'default_user_monthly_limit'}
                ),
                snapshot=baseline_snapshot,
                admission_blocked=admission_ready is True,
            )

        cycle = self._current_cycle(settings)
        if snapshot is None:
            snapshot_result = await self._get_financial_snapshot(
                org_id, settings, allow_stale=True
            )
        else:
            snapshot_result = BudgetFinancialSnapshotResult(
                snapshot=snapshot, status='live'
            )
        current_spend = _litellm_cycle_spend(settings, snapshot_result.snapshot)
        org_member_ids = await self._org_member_ids(org_id)
        unmapped_spend, unmapped_member_count = _litellm_unmapped_cycle_spend(
            settings, snapshot_result.snapshot, org_member_ids
        )
        users, users_total = await self._build_user_budget_rows(
            org_id,
            settings,
            snapshot_result.snapshot,
            users_page=users_page,
            users_per_page=users_per_page,
            users_search=users_search,
            users_status=users_status,
        )
        quint_oracle.log(
            'update_budget_settings',
            'org-budgets',
            org_id=quint_oracle.In('org', 'ORG_IDS'),
            budget_enabled=settings.enabled,
        )
        policy_comparison = _budget_policy_comparison(
            settings,
            overrides,
            org_member_ids,
            snapshot_result,
        )
        return {
            **await self._alert_availability(settings),
            'settings': settings,
            'thresholds': thresholds,
            'cycle': cycle,
            'current_spend': current_spend,
            'spend_status': snapshot_result.status,
            'spend_observed_at': (
                snapshot_result.snapshot.observed_at
                if snapshot_result.snapshot is not None
                else None
            ),
            'spend_error': snapshot_result.error,
            'unmapped_spend': unmapped_spend,
            'unmapped_member_count': unmapped_member_count,
            'users': users,
            'users_total': users_total,
            'users_page': users_page,
            'users_per_page': users_per_page,
            **policy_comparison,
        }

    async def upsert_user_override(
        self,
        org_id: UUID,
        user_id: UUID,
        monthly_limit: float | None,
        is_disabled: bool,
    ) -> OrgUserBudgetOverride:
        await self._reject_personal_org(org_id, 'upsert_user_override')
        settings = await self._get_or_create_settings(org_id, for_update=True)
        admission_ready = await self._begin_budget_edit(
            org_id, settings, await self._get_overrides(org_id)
        )
        override = await self.store.upsert_override(
            org_id=org_id,
            user_id=user_id,
            monthly_limit=monthly_limit,
            is_disabled=is_disabled,
        )
        overrides = await self._get_overrides(org_id)
        if admission_ready is not False:
            await self._sync_litellm_budgets(
                org_id,
                settings,
                overrides,
                clear_disabled=True,
                admission_blocked=admission_ready is True,
            )
        quint_oracle.log(
            'upsert_user_override',
            'org-budgets',
            org_id=quint_oracle.In('org', 'ORG_IDS'),
            override_count=len(overrides),
        )
        return override

    async def delete_user_override(self, org_id: UUID, user_id: UUID) -> None:
        await self._reject_personal_org(org_id, 'delete_user_override')
        settings = await self._get_or_create_settings(org_id, for_update=True)
        override = await self._get_override(org_id, user_id)
        if override is None:
            # The early return: no row, so no resync and no post-state count to read.
            quint_oracle.log(
                'delete_user_override',
                'org-budgets',
                org_id=quint_oracle.In('org', 'ORG_IDS'),
            )
            return
        admission_ready = await self._begin_budget_edit(
            org_id, settings, await self._get_overrides(org_id)
        )
        await self.store.delete_override(override)
        overrides = await self._get_overrides(org_id)
        if admission_ready is not False:
            await self._sync_litellm_budgets(
                org_id,
                settings,
                overrides,
                clear_disabled=True,
                admission_blocked=admission_ready is True,
            )
        quint_oracle.log(
            'delete_user_override',
            'org-budgets',
            org_id=quint_oracle.In('org', 'ORG_IDS'),
            override_count=len(overrides),
        )

    async def get_reconciliation_state(self, org_id: UUID) -> BudgetReconciliationState:
        settings = await self._get_settings_for_read(org_id)
        if settings.litellm_last_sync_status == 'error':
            return 'degraded'
        if settings.enabled and settings.litellm_last_sync_status != 'success':
            return 'pending'
        return 'healthy' if settings.enabled else 'inactive'

    def _default_settings(self, org_id: UUID) -> OrgBudgetSettings:
        """A transient defaults row, never added to the session.

        Read paths use this in place of the persisted row when an org has not
        configured budgets yet, so reading cannot create the row. The column
        defaults (``dict``/``list``/``0.0``) are only applied by Postgres at
        INSERT, so a transient instance must set them explicitly to match what a
        freshly created row would carry.
        """
        return OrgBudgetSettings(
            org_id=org_id,
            enabled=False,
            reset_day=1,
            monthly_limit=None,
            default_user_monthly_limit=None,
            cycle_start_at=_current_cycle_start(datetime.now(UTC), 1),
            cycle_start_spend=0.0,
            user_cycle_start_spend={},
            litellm_last_member_spend={},
            litellm_known_member_ids=[],
        )

    async def _get_settings_for_read(self, org_id: UUID) -> OrgBudgetSettings:
        """Read settings without ever writing a row.

        Returns the persisted (hydrated) row when it exists, otherwise a
        transient defaults row. This is the read-only counterpart of
        ``_get_or_create_settings``: because it never inserts, a read path cannot
        silently create a settings row for a personal workspace -- or any org --
        even if a caller forgets the ``_reject_personal_org`` guard.
        """
        settings = await self.store.get_settings(org_id)
        if settings:
            await self._hydrate_cycle_baselines(settings)
            return settings
        return self._default_settings(org_id)

    async def _get_or_create_settings(
        self, org_id: UUID, *, for_update: bool = False
    ) -> OrgBudgetSettings:
        settings = await self.store.get_settings(org_id, for_update=for_update)
        if settings:
            await self._hydrate_cycle_baselines(settings)
            return settings

        # Insert inside a savepoint so a concurrent creator's unique violation does
        # not poison the caller's transaction.
        try:
            async with self.db_session.begin_nested():
                settings = await self.store.create_settings(
                    org_id=org_id,
                    reset_day=1,
                    cycle_start_at=_current_cycle_start(datetime.now(UTC), 1),
                    thresholds=DEFAULT_THRESHOLDS,
                )
        except IntegrityError as exc:
            # Only a unique violation means the race was lost; create_settings also
            # writes rows carrying an FK to org.id, and a missing org must keep its
            # own error rather than be reported as a re-read that found nothing.
            if sqlstate(exc) != _UNIQUE_VIOLATION:
                raise
            # Finding the winner's committed row depends on READ COMMITTED, where
            # each statement takes a fresh snapshot. Under REPEATABLE READ this
            # session's snapshot predates that commit and the re-read returns None.
            settings = await self.store.get_settings(org_id, for_update=for_update)
            if settings is None:
                raise
            await self._hydrate_cycle_baselines(settings)
        return settings

    async def _hydrate_cycle_baselines(self, settings: OrgBudgetSettings) -> None:
        """Make the baseline table authoritative for the current cycle.

        Rows win over the ``user_cycle_start_spend`` JSON map, which is still
        dual-written during the compatibility window. Keys only the JSON holds
        (written by a release before migration 162) are imported so the window
        converges; the log line is the signal that it has not converged yet.
        """
        json_baselines = dict(settings.user_cycle_start_spend or {})
        rows = await self.store.get_cycle_baselines(
            settings.org_id, settings.cycle_start_at
        )
        json_only = {
            user_id: baseline
            for user_id, baseline in json_baselines.items()
            if user_id not in rows
        }
        if json_only:
            logger.info(
                'org_budget_cycle_baseline_json_only',
                extra={
                    'org_id': str(settings.org_id),
                    'cycle_start_at': str(settings.cycle_start_at),
                    'user_ids': sorted(json_only),
                },
            )
            await self.store.record_cycle_baselines(
                settings.org_id,
                settings.cycle_start_at,
                json_only,
                source=OrgBudgetCycleBaseline.SOURCE_IMPORTED,
                observed_at=datetime.now(UTC),
            )
        merged = {**json_baselines, **rows}
        if merged != json_baselines:
            settings.user_cycle_start_spend = merged

    async def _get_thresholds(self, org_id: UUID) -> list[OrgBudgetThreshold]:
        return await self.store.get_thresholds(org_id)

    async def _replace_thresholds(
        self,
        org_id: UUID,
        existing: list[OrgBudgetThreshold],
        new_thresholds,
    ) -> None:
        await self.store.replace_thresholds(org_id, existing, new_thresholds)

    async def _get_overrides(self, org_id: UUID) -> list[OrgUserBudgetOverride]:
        return await self.store.get_overrides(org_id)

    async def _get_override(
        self, org_id: UUID, user_id: UUID
    ) -> OrgUserBudgetOverride | None:
        return await self.store.get_override(org_id, user_id)

    def _current_cycle(self, settings: OrgBudgetSettings) -> BudgetCycle:
        start_at = settings.cycle_start_at
        end_at = settings.next_reset_at or _next_cycle_start(
            start_at, settings.reset_day
        )
        return BudgetCycle(start_at=start_at, end_at=end_at)

    async def _roll_cycle_if_needed(
        self,
        settings: OrgBudgetSettings,
        thresholds: list[OrgBudgetThreshold],
        overrides: list[OrgUserBudgetOverride],
        snapshot: LiteLlmFinancialSnapshot,
    ) -> bool:
        now = datetime.now(UTC)
        org_id = settings.org_id
        # Lock the row and re-read the anchor. If another run rolled while we were
        # reading LiteLLM, its snapshot anchors the new cycle -- not our stale one.
        locked_start = (
            await self.db_session.execute(
                select(OrgBudgetSettings.cycle_start_at)
                .where(OrgBudgetSettings.org_id == org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_start is not None and locked_start != settings.cycle_start_at:
            await self.store.refresh(settings)
            return False

        next_cycle = self._current_cycle(settings).end_at
        if now < next_cycle:
            return False

        # Settle the anchor at the current period in this single roll. Advancing only
        # one period per run would leave the anchor behind after a multi-period gap, so
        # every later maintenance run would roll again and re-anchor cycle_start_spend
        # to the current cumulative LiteLLM total -- forgiving spend incurred since
        # recovery and renewing the cap each time. Jumping straight to the current
        # period rolls at most once per period, so subsequent runs are no-ops.
        settings.cycle_start_at = _current_cycle_start(now, settings.reset_day)
        settings.next_reset_at = None
        settings.cycle_start_spend = snapshot.team_spend
        settings.user_cycle_start_spend = {
            user_id: member.spend for user_id, member in snapshot.members.items()
        }
        await self.store.record_cycle_baselines(
            org_id,
            settings.cycle_start_at,
            settings.user_cycle_start_spend,
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=snapshot.observed_at,
        )
        settings.litellm_known_member_ids = sorted(await self._org_member_ids(org_id))
        for threshold in thresholds:
            threshold.last_triggered_at = None
            threshold.last_triggered_cycle_start = None
            threshold.delivery_state = {}
        await self.store.flush()
        await self.store.refresh(settings)
        await self._sync_litellm_budgets(org_id, settings, overrides, snapshot=snapshot)
        return True

    def _cached_financial_snapshot(
        self, settings: OrgBudgetSettings
    ) -> LiteLlmFinancialSnapshot | None:
        observed_at = settings.litellm_last_spend_snapshot_at
        team_spend = settings.litellm_last_team_spend
        member_spend = settings.litellm_last_member_spend
        if (
            observed_at is None
            or team_spend is None
            or not isinstance(member_spend, dict)
        ):
            return None

        try:
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            members: dict[str, LiteLlmMemberFinancialSnapshot] = {}
            for user_id, spend in member_spend.items():
                if not isinstance(user_id, str) or not user_id:
                    raise ValueError('cached member id must be a non-empty string')
                members[user_id] = LiteLlmMemberFinancialSnapshot(
                    spend=_required_nonnegative_float(
                        spend, f'cached_members.{user_id}.spend'
                    ),
                    max_budget=None,
                    uses_shared_budget=True,
                )
            return LiteLlmFinancialSnapshot(
                team_spend=_required_nonnegative_float(team_spend, 'cached_team_spend'),
                team_max_budget=None,
                members=members,
                observed_at=observed_at,
            )
        except (TypeError, ValueError) as e:
            logger.warning(
                'org_budget_litellm_cached_snapshot_invalid',
                extra={'org_id': str(settings.org_id), 'error': str(e)},
            )
            return None

    async def _cache_financial_snapshot(
        self,
        settings: OrgBudgetSettings,
        snapshot: LiteLlmFinancialSnapshot,
    ) -> None:
        settings.litellm_last_spend_snapshot_at = snapshot.observed_at
        settings.litellm_last_team_spend = snapshot.team_spend
        settings.litellm_last_member_spend = {
            user_id: member.spend for user_id, member in snapshot.members.items()
        }
        await self.store.flush()

    async def _get_financial_snapshot(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        *,
        allow_stale: bool,
        require_complete_membership: bool = False,
    ) -> BudgetFinancialSnapshotResult:
        error: str | None = None
        try:
            financial_data = await self._fetch_litellm_financial_data(org_id)
            snapshot = _parse_litellm_financial_snapshot(financial_data)
            org_member_ids = await self._org_member_ids(org_id)
            missing_member_ids = sorted(org_member_ids - set(snapshot.members))
            if missing_member_ids and require_complete_membership:
                raise ValueError(
                    'LiteLLM spend data is missing organization members: '
                    + ', '.join(missing_member_ids)
                )
        except Exception as e:
            error = str(e)
            logger.warning(
                'org_budget_litellm_financial_data_fetch_failed',
                extra={'org_id': str(org_id), 'error': error},
            )
        else:
            if not missing_member_ids:
                await self._cache_financial_snapshot(settings, snapshot)
            else:
                logger.warning(
                    'org_budget_litellm_membership_snapshot_incomplete',
                    extra={
                        'org_id': str(org_id),
                        'user_ids': missing_member_ids,
                    },
                )
            return BudgetFinancialSnapshotResult(snapshot=snapshot, status='live')

        cached = self._cached_financial_snapshot(settings) if allow_stale else None
        if cached is not None:
            return BudgetFinancialSnapshotResult(
                snapshot=cached,
                status='stale',
                error=error,
            )
        return BudgetFinancialSnapshotResult(
            snapshot=None,
            status='unavailable',
            error=error,
        )

    async def _fetch_litellm_financial_data(self, org_id: UUID) -> dict:
        for attempt in range(1, LITELLM_FINANCIAL_READ_MAX_ATTEMPTS + 1):
            try:
                return await LiteLlmManager.get_team_members_financial_data(str(org_id))
            except Exception as error:
                if (
                    attempt == LITELLM_FINANCIAL_READ_MAX_ATTEMPTS
                    or not _is_retryable_litellm_read_error(error)
                ):
                    raise
                logger.warning(
                    'org_budget_litellm_financial_data_fetch_retry',
                    extra={
                        'org_id': str(org_id),
                        'attempt': attempt,
                        'error': str(error),
                    },
                )
                await asyncio.sleep(
                    LITELLM_FINANCIAL_READ_RETRY_DELAY_SECONDS * attempt
                )

        raise RuntimeError('unreachable')

    @staticmethod
    def _snapshot_unavailable_detail() -> str:
        return 'Fresh LiteLLM spend data is required for this budget change.'

    def _budget_row_matches_status(self, row: dict, users_status: str | None) -> bool:
        status_value = (users_status or '').strip().lower()
        if not status_value:
            return True

        effective_limit = row['effective_monthly_limit']
        is_disabled = row['is_disabled']
        has_limit = (
            not is_disabled and effective_limit is not None and effective_limit > 0
        )
        current_spend = row['current_spend']

        if status_value == 'unavailable':
            return current_spend is None
        if status_value == 'disabled':
            return is_disabled
        if status_value == 'nocap':
            return not is_disabled and (effective_limit is None or effective_limit <= 0)
        if current_spend is None:
            return False
        if status_value == 'overcap':
            return has_limit and current_spend > effective_limit
        if status_value == 'over90':
            return has_limit and current_spend >= effective_limit * 0.9
        if status_value == 'over80':
            return has_limit and current_spend >= effective_limit * 0.8
        if status_value == 'ontrack':
            return has_limit and current_spend < effective_limit * 0.8
        return True

    async def _build_user_budget_rows(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        snapshot: LiteLlmFinancialSnapshot | None,
        users_page: int,
        users_per_page: int,
        users_search: str | None,
        users_status: str | None,
    ) -> tuple[list[dict], int]:
        members = snapshot.members if snapshot is not None else {}

        query = (
            select(OrgMember, User, OrgUserBudgetOverride)
            .join(User, OrgMember.user_id == User.id)
            .outerjoin(
                OrgUserBudgetOverride,
                and_(
                    OrgUserBudgetOverride.org_id == org_id,
                    OrgUserBudgetOverride.user_id == OrgMember.user_id,
                ),
            )
            .where(OrgMember.org_id == org_id)
            .order_by(User.email.asc(), User.id.asc())
        )

        search_value = (users_search or '').strip()
        if search_value:
            escaped = escape_ilike(search_value)
            pattern = f'%{escaped}%'
            query = query.where(
                or_(
                    User.email.ilike(pattern, escape='\\'),
                    User.git_user_name.ilike(pattern, escape='\\'),
                )
            )

        result = await self.db_session.execute(query)
        rows = []
        for org_member, user, override in result:
            user_id = str(org_member.user_id)
            effective_limit, is_disabled, is_override = _effective_user_budget_limit(
                override, settings.default_user_monthly_limit
            )
            row = {
                'user_id': user_id,
                'user_email': user.email,
                'user_name': user.git_user_name,
                'current_spend': _litellm_member_cycle_spend(
                    settings, user_id, members.get(user_id)
                ),
                'monthly_limit': override.monthly_limit if override else None,
                'effective_monthly_limit': effective_limit,
                'is_disabled': is_disabled,
                'is_override': is_override,
            }
            if self._budget_row_matches_status(row, users_status):
                rows.append(row)

        total = len(rows)
        offset = (users_page - 1) * users_per_page
        return rows[offset : offset + users_per_page], total

    async def get_user_budget_row(self, org_id: UUID, user_id: UUID) -> dict | None:
        await self._reject_personal_org(org_id, 'get_user_budget_row')
        settings = await self._get_settings_for_read(org_id)
        overrides = await self._get_overrides(org_id)
        snapshot_result = await self._get_financial_snapshot(
            org_id, settings, allow_stale=True
        )
        members = (
            snapshot_result.snapshot.members
            if snapshot_result.snapshot is not None
            else {}
        )

        result = await self.db_session.execute(
            select(OrgMember, User)
            .join(User, OrgMember.user_id == User.id)
            .where(OrgMember.org_id == org_id)
            .where(OrgMember.user_id == user_id)
        )
        row = result.one_or_none()
        if not row:
            return None

        org_member, user = row
        override = next(
            (override for override in overrides if override.user_id == user_id),
            None,
        )
        effective_limit, is_disabled, is_override = _effective_user_budget_limit(
            override, settings.default_user_monthly_limit
        )
        user_id_str = str(user_id)
        user_row = {
            'user_id': str(org_member.user_id),
            'user_email': user.email,
            'user_name': user.git_user_name,
            'current_spend': _litellm_member_cycle_spend(
                settings, user_id_str, members.get(user_id_str)
            ),
            'monthly_limit': override.monthly_limit if override else None,
            'effective_monthly_limit': effective_limit,
            'is_disabled': is_disabled,
            'is_override': is_override,
        }
        policy_comparison = _budget_policy_comparison(
            settings,
            overrides,
            await self._org_member_ids(org_id),
            snapshot_result,
        )
        user_row.update(
            reconciliation_state=policy_comparison['reconciliation_state'],
            reconciliation_error=policy_comparison['reconciliation_error'],
            applied_at=policy_comparison['applied_at'],
        )
        return user_row

    async def get_my_budget(
        self, org_id: UUID, user_id: UUID, include_spend: bool = True
    ) -> dict:
        """Read-only view of one member's own budget for the current cycle.

        Unlike ``get_user_budget_row`` this never creates a settings row, so it
        is safe to call for orgs that have not configured budgets.
        ``include_spend=False`` answers only whether budgets are enabled,
        without reading LiteLLM.
        """
        if await self._is_personal_org(org_id):
            return {'enabled': False}
        settings = await self.store.get_settings(org_id)
        if settings is None or not settings.enabled:
            return {'enabled': False}
        if not include_spend:
            return {'enabled': True}

        await self._hydrate_cycle_baselines(settings)
        override = await self._get_override(org_id, user_id)
        snapshot_result = await self._get_financial_snapshot(
            org_id, settings, allow_stale=True
        )
        snapshot = snapshot_result.snapshot
        effective_limit, is_disabled, is_override = _effective_user_budget_limit(
            override, settings.default_user_monthly_limit
        )
        user_id_str = str(user_id)
        cycle = self._current_cycle(settings)
        return {
            'enabled': True,
            'monthly_limit': effective_limit,
            'is_disabled': is_disabled,
            'is_override': is_override,
            # The settings row is rewritten on every spend snapshot, so only an
            # override carries a meaningful "set on" date.
            'limit_updated_at': override.updated_at if override else None,
            'current_spend': _litellm_member_cycle_spend(
                settings,
                user_id_str,
                snapshot.members.get(user_id_str) if snapshot is not None else None,
            ),
            'cycle_start_at': cycle.start_at,
            'cycle_end_at': cycle.end_at,
            'spend_status': snapshot_result.status,
            'spend_observed_at': (
                snapshot.observed_at if snapshot is not None else None
            ),
        }

    async def _record_litellm_sync(
        self,
        settings: OrgBudgetSettings,
        status_value: str,
        error: str | None = None,
    ) -> None:
        settings.litellm_last_sync_at = datetime.now(UTC)
        settings.litellm_last_sync_status = status_value
        settings.litellm_last_sync_error = error
        await self.store.flush()

    async def _begin_budget_edit(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        overrides: list[OrgUserBudgetOverride],
    ) -> bool:
        try:
            return await self._block_litellm_admission(
                org_id, settings, reject_on_failure=True
            )
        except BudgetChangeRejectedError as error:
            snapshot = await self._get_financial_snapshot(
                org_id, settings, allow_stale=False
            )
            comparison = _budget_policy_comparison(
                settings,
                overrides,
                await self._org_member_ids(org_id),
                snapshot,
            )
            error.previous_policy_verified = comparison['budget_policy_matches'] is True
            raise

    async def _block_litellm_admission(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        *,
        reject_on_failure: bool = False,
    ) -> bool:
        team_id = str(org_id)
        try:
            await LiteLlmManager.set_team_blocked(team_id, True)
        except Exception as error:
            error_message = f'admission_block_failed: {error}'
            logger.warning(
                'org_budget_litellm_admission_block_failed',
                extra={'org_id': team_id, 'error': str(error)},
            )
            fallback_failed = False
            try:
                await LiteLlmManager.block_team(team_id)
            except Exception as fallback_error:
                fallback_failed = True
                error_message = (
                    f'admission_fallback_failed: {fallback_error}; {error_message}'
                )
                logger.error(
                    'org_budget_litellm_admission_fallback_failed',
                    extra={'org_id': team_id, 'error': str(fallback_error)},
                )
            # Quarantine is not a verified policy; leave reconciliation retryable.
            await self._record_litellm_sync(settings, 'error', error_message[:500])
            if fallback_failed and reject_on_failure:
                raise BudgetChangeRejectedError() from error
            return False

        await self._record_litellm_sync(settings, 'pending')
        return True

    async def _restore_litellm_admission(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
    ) -> bool:
        try:
            await LiteLlmManager.set_team_blocked(str(org_id), False)
        except Exception as error:
            error_message = f'admission_restore_failed: {error}'
            logger.warning(
                'org_budget_litellm_admission_restore_failed',
                extra={'org_id': str(org_id), 'error': str(error)},
            )
            await self._record_litellm_sync(settings, 'error', error_message[:500])
            return False
        return True

    async def _budget_policy_matches_snapshot(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        overrides: list[OrgUserBudgetOverride],
        snapshot: LiteLlmFinancialSnapshot,
    ) -> bool:
        comparison = _budget_policy_comparison(
            settings,
            overrides,
            await self._org_member_ids(org_id),
            BudgetFinancialSnapshotResult(snapshot=snapshot, status='live'),
        )
        return comparison['budget_policy_matches'] is True

    @staticmethod
    def _needs_litellm_sync(
        settings: OrgBudgetSettings, overrides: list[OrgUserBudgetOverride]
    ) -> bool:
        return (
            settings.enabled
            or settings.default_user_monthly_limit is not None
            or bool(overrides)
            or settings.litellm_last_sync_status in {'pending', 'success', 'error'}
        )

    async def _org_member_ids(self, org_id: UUID) -> set[str]:
        member_result = await self.db_session.execute(
            select(OrgMember.user_id).where(OrgMember.org_id == org_id)
        )
        return {str(user_id) for user_id in member_result.scalars().all()}

    async def _repair_missing_members_for_cycle(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        overrides: list[OrgUserBudgetOverride],
        snapshot: LiteLlmFinancialSnapshot,
    ) -> BudgetFinancialSnapshotResult:
        org_member_ids = await self._org_member_ids(org_id)
        missing_member_ids = sorted(org_member_ids - set(snapshot.members))
        if not missing_member_ids:
            return BudgetFinancialSnapshotResult(snapshot=snapshot, status='live')

        override_map = {str(override.user_id): override for override in overrides}
        try:
            for user_id in missing_member_ids:
                if not await LiteLlmManager.user_exists(user_id):
                    raise RuntimeError(
                        f'LiteLLM user {user_id} is missing; explicit key repair is required'
                    )

                effective_limit, is_disabled, _ = _effective_user_budget_limit(
                    override_map.get(user_id), settings.default_user_monthly_limit
                )
                member_budget = None if is_disabled else effective_limit
                await LiteLlmManager.add_user_to_team(
                    user_id,
                    str(org_id),
                    member_budget,
                )
        except Exception as error:
            logger.warning(
                'org_budget_litellm_cycle_membership_repair_failed',
                extra={'org_id': str(org_id), 'error': str(error)},
            )
            return BudgetFinancialSnapshotResult(
                snapshot=None,
                status='unavailable',
                error=f'membership_repair_failed: {error}',
            )

        result = await self._get_financial_snapshot(
            org_id,
            settings,
            allow_stale=False,
            require_complete_membership=True,
        )
        if result.snapshot is None:
            return BudgetFinancialSnapshotResult(
                snapshot=None,
                status='unavailable',
                error=(
                    'membership_repair_verification_failed: '
                    f'{result.error or "unknown"}'
                ),
            )
        return result

    async def _sync_litellm_budgets(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        overrides: list[OrgUserBudgetOverride],
        clear_disabled: bool = False,
        snapshot: LiteLlmFinancialSnapshot | None = None,
        admission_blocked: bool = False,
    ) -> LiteLlmFinancialSnapshot | None:
        if not clear_disabled and not self._needs_litellm_sync(settings, overrides):
            await self._record_litellm_sync(settings, 'skipped')
            return snapshot

        # A verified unchanged disabled policy does not need an admission outage.
        if not settings.enabled and settings.litellm_last_sync_status == 'success':
            if snapshot is None:
                snapshot = (
                    await self._get_financial_snapshot(
                        org_id, settings, allow_stale=False
                    )
                ).snapshot
            if snapshot is not None and await self._budget_policy_matches_snapshot(
                org_id, settings, overrides, snapshot
            ):
                return snapshot

        if not admission_blocked and not await self._block_litellm_admission(
            org_id, settings
        ):
            return snapshot

        sync_errors: list[str] = []
        if snapshot is None:
            snapshot_result = await self._get_financial_snapshot(
                org_id, settings, allow_stale=False
            )
            snapshot = snapshot_result.snapshot
            if snapshot is None:
                error_message = f'fetch_failed: {snapshot_result.error or "unknown"}'
                await self._record_litellm_sync(settings, 'error', error_message[:500])
                return None

        members = snapshot.members

        org_member_ids = await self._org_member_ids(org_id)
        litellm_member_ids = set(members)
        known_member_ids = set(settings.litellm_known_member_ids or [])
        new_member_ids = org_member_ids - known_member_ids
        missing_member_ids = sorted(org_member_ids - litellm_member_ids)
        unmanaged_member_ids = sorted(litellm_member_ids - org_member_ids)
        for user_id in missing_member_ids:
            sync_errors.append(f'member_missing_from_litellm: {user_id}')
        if unmanaged_member_ids:
            logger.info(
                'org_budget_litellm_unmanaged_members',
                extra={
                    'org_id': str(org_id),
                    'user_ids': unmanaged_member_ids,
                },
            )

        if settings.enabled and settings.monthly_limit:
            # Anchor LiteLLM budgets to our cycle start spend so resets stay aligned.
            expected_team_budget = settings.cycle_start_spend + settings.monthly_limit
            try:
                await LiteLlmManager.update_team(
                    str(org_id),
                    team_alias=None,
                    max_budget=expected_team_budget,
                )
            except Exception as e:
                sync_errors.append(f'team_update_failed: {e}')
                logger.warning(
                    'org_budget_litellm_team_update_failed',
                    extra={'org_id': str(org_id), 'error': str(e)},
                )
        else:
            expected_team_budget = None
            try:
                await LiteLlmManager.update_team(
                    str(org_id),
                    team_alias=None,
                    max_budget=None,
                    clear_budget=True,
                )
            except Exception as e:
                sync_errors.append(f'team_clear_failed: {e}')
                logger.warning(
                    'org_budget_litellm_team_clear_failed',
                    extra={'org_id': str(org_id), 'error': str(e)},
                )

        override_map = {str(o.user_id): o for o in overrides}
        existing_user_baselines = settings.user_cycle_start_spend or {}
        active_user_baselines: dict[str, float] = {}
        added_baselines: dict[str, float] = {}
        recovered_baselines: dict[str, float] = {}
        expected_member_budgets: dict[str, float | None] = {}

        for user_id in sorted(org_member_ids & litellm_member_ids):
            info = members[user_id]
            baseline = existing_user_baselines.get(user_id)
            if baseline is None:
                baseline = info.spend
                added_baselines[user_id] = baseline
                if settings.enabled and user_id not in new_member_ids:
                    recovered_baselines[user_id] = added_baselines.pop(user_id)
                    # Legacy rows: migration 149 added baselines without a
                    # backfill and migration 156 marked every member known, so
                    # there is no cycle-start history. Anchor to live cumulative
                    # spend instead of preserving a stale LiteLLM cap forever.
                    logger.warning(
                        'org_budget_member_cycle_baseline_recovered',
                        extra={
                            'org_id': str(org_id),
                            'user_id': user_id,
                            'baseline': baseline,
                            'source': 'upgrade_recovery',
                        },
                    )
            active_user_baselines[user_id] = baseline

            override = override_map.get(user_id)
            effective_limit, is_disabled, _ = _effective_user_budget_limit(
                override, settings.default_user_monthly_limit
            )
            if is_disabled:
                max_budget_in_team = None
                clear_budget = True
            elif effective_limit is not None:
                max_budget_in_team = _member_cap(baseline, effective_limit)
                clear_budget = False
            else:
                max_budget_in_team = None
                clear_budget = True
            expected_member_budgets[user_id] = max_budget_in_team
            try:
                await LiteLlmManager.update_user_in_team(
                    user_id,
                    str(org_id),
                    max_budget=max_budget_in_team,
                    clear_budget=clear_budget,
                )
            except Exception as e:
                sync_errors.append(f'user_update_failed: {user_id}: {e}')
                logger.warning(
                    'org_budget_litellm_user_update_failed',
                    extra={
                        'org_id': str(org_id),
                        'user_id': user_id,
                        'error': str(e),
                    },
                )

        for user_id in missing_member_ids:
            baseline = existing_user_baselines.get(user_id)
            if baseline is not None:
                active_user_baselines[user_id] = baseline

        for user_id in unmanaged_member_ids:
            baseline = existing_user_baselines.get(user_id)
            if baseline is not None:
                active_user_baselines[user_id] = baseline

        settings.user_cycle_start_spend = active_user_baselines
        for initialized, source in (
            (added_baselines, OrgBudgetCycleBaseline.SOURCE_MEMBER_ADDED),
            (recovered_baselines, OrgBudgetCycleBaseline.SOURCE_UPGRADE_RECOVERY),
        ):
            await self.store.record_cycle_baselines(
                org_id,
                settings.cycle_start_at,
                initialized,
                source=source,
                observed_at=snapshot.observed_at,
            )
        settings.litellm_known_member_ids = sorted(
            (known_member_ids & org_member_ids) | (org_member_ids & litellm_member_ids)
        )

        readback_result = await self._get_financial_snapshot(
            org_id, settings, allow_stale=False
        )
        readback = readback_result.snapshot
        if readback is not None:
            snapshot = readback
            sync_errors.extend(
                _budget_sync_readback_errors(
                    readback,
                    expected_team_budget,
                    expected_member_budgets,
                )
            )
        else:
            sync_errors.append(
                f'verification_fetch_failed: {readback_result.error or "unknown"}'
            )

        if not sync_errors and not await self._restore_litellm_admission(
            org_id, settings
        ):
            return snapshot

        if sync_errors:
            summary = sync_errors[0]
            if len(sync_errors) > 1:
                summary = f'{summary} (+{len(sync_errors) - 1} more)'
            await self._record_litellm_sync(settings, 'error', summary[:500])
        else:
            await self._record_litellm_sync(settings, 'success')
        return snapshot

    async def _maybe_send_alerts(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        thresholds: list[OrgBudgetThreshold],
        current_spend: float,
        cycle_start: datetime,
    ) -> None:
        if not settings.enabled or not settings.monthly_limit:
            return

        if settings.monthly_limit <= 0:
            return

        percentage = (current_spend / settings.monthly_limit) * 100
        now = datetime.now(UTC)

        triggered = False
        for threshold in thresholds:
            if percentage < threshold.percentage:
                continue
            if threshold.last_triggered_cycle_start == cycle_start:
                continue

            if (threshold.delivery_state or {}).get(
                'cycle_start'
            ) != cycle_start.isoformat():
                threshold.delivery_state = {'cycle_start': cycle_start.isoformat()}
            delivered = await self._send_alerts(
                org_id,
                settings,
                threshold,
                current_spend,
                percentage,
            )
            if delivered:
                threshold.last_triggered_at = now
                threshold.last_triggered_cycle_start = cycle_start
            triggered = True

        if triggered:
            await self.store.flush()

    async def _send_alerts(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        threshold: OrgBudgetThreshold,
        current_spend: float,
        percentage: float,
    ) -> bool:
        org_name = await self._get_org_name(org_id)
        progress = dict(threshold.delivery_state or {})
        delivered = True
        if threshold.email_enabled:
            recipients = await self._get_admin_emails(org_id)
            sent_to = set(progress.get('email_recipients', []))
            for recipient in set(recipients) - sent_to:
                try:
                    success = await asyncio.to_thread(
                        SMTPEmailService.send_budget_alert_email,
                        [recipient],
                        org_name=org_name,
                        percentage=percentage,
                        current_spend=current_spend,
                        monthly_limit=settings.monthly_limit or 0,
                        threshold=threshold.percentage,
                    )
                except Exception:
                    logger.exception('Budget alert email delivery failed')
                    success = False
                if success:
                    sent_to.add(recipient)
            progress['email_recipients'] = sorted(sent_to)
            delivered = bool(recipients) and set(recipients).issubset(sent_to)

        if threshold.slack_enabled:
            if not progress.get('slack'):
                progress['slack'] = await self._send_slack_alert(
                    org_name,
                    settings,
                    threshold.percentage,
                    current_spend,
                    percentage,
                )
            delivered = delivered and bool(progress['slack'])

        threshold.delivery_state = progress
        return delivered

    async def _get_org_name(self, org_id: UUID) -> str:
        result = await self.db_session.execute(select(Org.name).where(Org.id == org_id))
        return result.scalar_one_or_none() or 'your organization'

    async def _get_admin_emails(self, org_id: UUID) -> list[str]:
        query = (
            select(User.email)
            .join(OrgMember, OrgMember.user_id == User.id)
            .join(Role, Role.id == OrgMember.role_id)
            .where(OrgMember.org_id == org_id)
            .where(Role.name.in_([RoleName.ADMIN.value, RoleName.OWNER.value]))
        )
        result = await self.db_session.execute(query)
        return [row.email for row in result if row.email]

    async def _send_slack_alert(
        self,
        org_name: str,
        settings: OrgBudgetSettings,
        threshold: int,
        current_spend: float,
        percentage: float,
    ) -> bool:
        if not settings.slack_channel:
            return False
        team_id = await self._resolve_slack_team_id(settings.slack_team_id)
        token = await self._get_slack_bot_token(team_id) if team_id else None
        if not token:
            return False

        client = AsyncWebClient(token=token)
        message = (
            f':warning: OpenHands budget alert for *{org_name}*\n'
            f'Threshold: *{threshold}%*\n'
            f'Current spend: *${current_spend:,.2f}* '
            f'({percentage:.1f}% of ${settings.monthly_limit:,.2f})'
        )
        try:
            result = await client.chat_postMessage(
                channel=settings.slack_channel,
                text=message,
            )
            return bool(result.get('ok'))
        except Exception as e:
            logger.warning(
                'Slack budget alert failed',
                extra={'error': str(e), 'team_id': team_id},
            )
            return False

    async def _get_slack_bot_token(self, team_id: str | None) -> str | None:
        if not SLACK_AVAILABLE or not is_slack_configured():
            return None
        team_id = await self._resolve_slack_team_id(team_id)
        if not team_id:
            return None
        result = await self.db_session.execute(
            select(SlackTeam.bot_access_token).where(SlackTeam.team_id == team_id)
        )
        return result.scalar_one_or_none() or None

    async def _alert_availability(self, settings: OrgBudgetSettings) -> dict[str, bool]:
        return {
            'email_alerts_available': SMTPEmailService.is_configured(),
            'slack_integration_configured': SLACK_AVAILABLE and is_slack_configured(),
            'slack_workspace_connected': bool(
                await self._get_slack_bot_token(settings.slack_team_id)
            ),
        }

    async def _validate_alert_settings(
        self,
        settings: OrgBudgetSettings,
        update_data: OrgBudgetSettingsUpdate,
        existing: list[OrgBudgetThreshold],
    ) -> None:
        thresholds = update_data.thresholds
        if thresholds is None:
            return
        previous = {threshold.percentage: threshold for threshold in existing}

        def activates(channel: str) -> bool:
            return any(
                getattr(threshold, channel)
                and not getattr(previous.get(threshold.percentage), channel, False)
                for threshold in thresholds
            )

        def reject(detail: str) -> None:
            quint_oracle.log(
                'update_budget_settings',
                'org-budgets',
                org_id=quint_oracle.In('org', 'ORG_IDS'),
                outcome='rejected',
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

        if activates('email_enabled') and not SMTPEmailService.is_configured():
            reject('Email budget alerts require SMTP configuration.')
        if not any(threshold.slack_enabled for threshold in thresholds):
            return
        fields = update_data.model_fields_set
        team_id = (
            update_data.slack_team_id
            if 'slack_team_id' in fields
            else settings.slack_team_id
        )
        channel = (
            update_data.slack_channel
            if 'slack_channel' in fields
            else settings.slack_channel
        )
        destination_changed = (
            team_id != settings.slack_team_id or channel != settings.slack_channel
        )
        if not activates('slack_enabled') and not destination_changed:
            return
        if not await self._get_slack_bot_token(team_id):
            reject('Slack budget alerts require a connected Slack integration.')
        if not channel:
            reject('Select a Slack channel for budget alerts.')

    async def _resolve_slack_team_id(self, team_id: str | None) -> str | None:
        if team_id:
            return team_id
        result = await self.db_session.execute(select(SlackTeam.team_id))
        team_ids = [row.team_id for row in result]
        if len(team_ids) == 1:
            return team_ids[0]
        if team_ids:
            logger.warning(
                'Multiple Slack teams configured; set slack_team_id to enable alerts'
            )
        return None


class OrgBudgetServiceInjector(Injector[OrgBudgetService]):
    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[OrgBudgetService, None]:
        from openhands.app_server.config import get_db_session

        async with get_db_session(state, request) as db_session:
            yield OrgBudgetService(db_session=db_session)
