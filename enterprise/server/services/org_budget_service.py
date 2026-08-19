from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import AsyncGenerator
from uuid import UUID

from fastapi import HTTPException, Request, status
from server.auth.authorization import RoleName
from server.services.smtp_email_service import SMTPEmailService
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from storage.lite_llm_manager import LiteLlmManager
from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from storage.slack_team import SlackTeam
from storage.user import User

from openhands.app_server.services.injector import Injector, InjectorState
from openhands.app_server.utils.logger import openhands_logger as logger

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


@dataclass
class BudgetCycle:
    start_at: datetime
    end_at: datetime


def _add_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _subtract_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _current_cycle_start(now: datetime, reset_day: int) -> datetime:
    if now.day >= reset_day:
        return datetime(now.year, now.month, reset_day, tzinfo=UTC)
    prev_year, prev_month = _subtract_month(now.year, now.month)
    return datetime(prev_year, prev_month, reset_day, tzinfo=UTC)


def _next_cycle_start(cycle_start: datetime, reset_day: int) -> datetime:
    year, month = _add_month(cycle_start.year, cycle_start.month)
    return datetime(year, month, reset_day, tzinfo=UTC)


def _float_or_zero(value) -> float:
    return float(value or 0.0)


def _litellm_cycle_spend(
    settings: OrgBudgetSettings,
    financial_data: dict | None,
) -> float:
    if not financial_data:
        return 0.0
    cumulative_spend = _float_or_zero(financial_data.get('team_spend'))
    return max(cumulative_spend - _float_or_zero(settings.cycle_start_spend), 0.0)


def _litellm_member_cycle_spend(
    settings: OrgBudgetSettings,
    user_id: str,
    member_info: dict | None,
) -> float:
    if not member_info:
        return 0.0
    cumulative_spend = _float_or_zero(member_info.get('spend'))
    baseline = _float_or_zero((settings.user_cycle_start_spend or {}).get(user_id))
    return max(cumulative_spend - baseline, 0.0)


def _effective_user_budget_limit(
    override: OrgUserBudgetOverride | None,
    default_limit: float | None,
) -> tuple[float | None, bool, bool]:
    if override:
        if override.is_disabled:
            return None, True, True
        return override.monthly_limit, False, True
    return default_limit, False, False


def _escape_ilike(value: str) -> str:
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


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

    async def _reject_personal_org(self, org_id: UUID) -> None:
        if await self._is_personal_org(org_id):
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
        await self._reject_personal_org(org_id)
        settings = await self._get_or_create_settings(org_id)
        thresholds = await self._get_thresholds(org_id)
        cycle = self._current_cycle(settings)

        financial_data = await self._fetch_budget_financial_data(org_id)
        current_spend = await self._get_cycle_spend(org_id, settings, financial_data)
        users, users_total = await self._build_user_budget_rows(
            org_id,
            settings,
            financial_data,
            users_page=users_page,
            users_per_page=users_per_page,
            users_search=users_search,
            users_status=users_status,
        )
        return {
            'settings': settings,
            'thresholds': thresholds,
            'cycle': cycle,
            'current_spend': current_spend,
            'users': users,
            'users_total': users_total,
            'users_page': users_page,
            'users_per_page': users_per_page,
        }

    async def run_budget_maintenance(self, org_id: UUID) -> dict:
        if await self._is_personal_org(org_id):
            return {
                'cycle_start_at': None,
                'cycle_end_at': None,
                'cycle_rolled': False,
                'current_spend': 0.0,
                'skipped': 'personal_org',
            }

        settings = await self._get_or_create_settings(org_id)
        thresholds = await self._get_thresholds(org_id)
        overrides = await self._get_overrides(org_id)
        cycle = self._current_cycle(settings)

        cycle_rolled = await self._roll_cycle_if_needed(settings, thresholds, overrides)
        if cycle_rolled:
            cycle = self._current_cycle(settings)

        financial_data = await self._fetch_budget_financial_data(org_id)
        current_spend = await self._get_cycle_spend(org_id, settings, financial_data)
        await self._maybe_send_alerts(
            org_id,
            settings,
            thresholds,
            current_spend,
            cycle.start_at,
        )
        if not cycle_rolled:
            await self._sync_litellm_budgets(
                org_id, settings, overrides, financial_data=financial_data
            )

        return {
            'cycle_start_at': cycle.start_at,
            'cycle_end_at': cycle.end_at,
            'cycle_rolled': cycle_rolled,
            'current_spend': current_spend,
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
        await self._reject_personal_org(org_id)
        settings = await self._get_or_create_settings(org_id)
        thresholds = await self._get_thresholds(org_id)
        overrides = await self._get_overrides(org_id)

        fields_set = update_data.model_fields_set
        reset_day_changed = False
        previous_enabled = settings.enabled

        if 'enabled' in fields_set:
            settings.enabled = update_data.enabled
        if 'monthly_limit' in fields_set:
            settings.monthly_limit = update_data.monthly_limit
        if 'reset_day' in fields_set:
            settings.reset_day = update_data.reset_day
            reset_day_changed = True
        if 'default_user_monthly_limit' in fields_set:
            settings.default_user_monthly_limit = update_data.default_user_monthly_limit
        if 'slack_channel' in fields_set:
            settings.slack_channel = update_data.slack_channel
        if 'slack_team_id' in fields_set:
            settings.slack_team_id = update_data.slack_team_id

        if settings.enabled and (
            settings.monthly_limit is None or settings.monthly_limit <= 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='monthly_limit is required when budgets are enabled',
            )

        if reset_day_changed or (not previous_enabled and settings.enabled):
            settings.cycle_start_at = _current_cycle_start(
                datetime.now(UTC), settings.reset_day
            )
            settings.cycle_start_spend = await self._fetch_team_spend(org_id)
            settings.user_cycle_start_spend = {}

        if 'thresholds' in fields_set and update_data.thresholds is not None:
            await self._replace_thresholds(org_id, thresholds, update_data.thresholds)
            thresholds = await self._get_thresholds(org_id)

        await self.store.flush()
        await self.store.refresh(settings)

        financial_data = await self._sync_litellm_budgets(
            org_id,
            settings,
            overrides,
            clear_disabled=previous_enabled and not settings.enabled,
        )

        cycle = self._current_cycle(settings)
        if financial_data is None:
            financial_data = await self._fetch_budget_financial_data(org_id)
        current_spend = await self._get_cycle_spend(org_id, settings, financial_data)
        users, users_total = await self._build_user_budget_rows(
            org_id,
            settings,
            financial_data,
            users_page=users_page,
            users_per_page=users_per_page,
            users_search=users_search,
            users_status=users_status,
        )
        return {
            'settings': settings,
            'thresholds': thresholds,
            'cycle': cycle,
            'current_spend': current_spend,
            'users': users,
            'users_total': users_total,
            'users_page': users_page,
            'users_per_page': users_per_page,
        }

    async def upsert_user_override(
        self,
        org_id: UUID,
        user_id: UUID,
        monthly_limit: float | None,
        is_disabled: bool,
    ) -> OrgUserBudgetOverride:
        await self._reject_personal_org(org_id)
        override = await self.store.upsert_override(
            org_id=org_id,
            user_id=user_id,
            monthly_limit=monthly_limit,
            is_disabled=is_disabled,
        )
        settings = await self._get_or_create_settings(org_id)
        overrides = await self._get_overrides(org_id)
        await self._sync_litellm_budgets(org_id, settings, overrides)
        return override

    async def delete_user_override(self, org_id: UUID, user_id: UUID) -> None:
        await self._reject_personal_org(org_id)
        override = await self._get_override(org_id, user_id)
        if override is None:
            return
        await self.store.delete_override(override)
        settings = await self._get_or_create_settings(org_id)
        overrides = await self._get_overrides(org_id)
        await self._sync_litellm_budgets(org_id, settings, overrides)

    async def _get_or_create_settings(self, org_id: UUID) -> OrgBudgetSettings:
        settings = await self.store.get_settings(org_id)
        if settings:
            return settings

        return await self.store.create_settings(
            org_id=org_id,
            reset_day=1,
            cycle_start_at=_current_cycle_start(datetime.now(UTC), 1),
            thresholds=DEFAULT_THRESHOLDS,
        )

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
        end_at = _next_cycle_start(start_at, settings.reset_day)
        return BudgetCycle(start_at=start_at, end_at=end_at)

    async def _roll_cycle_if_needed(
        self,
        settings: OrgBudgetSettings,
        thresholds: list[OrgBudgetThreshold],
        overrides: list[OrgUserBudgetOverride],
    ) -> bool:
        now = datetime.now(UTC)
        next_cycle = _next_cycle_start(settings.cycle_start_at, settings.reset_day)
        if now < next_cycle:
            return False

        settings.cycle_start_at = _current_cycle_start(now, settings.reset_day)
        org_id = settings.org_id
        settings.cycle_start_spend = await self._fetch_team_spend(org_id)
        settings.user_cycle_start_spend = {}
        for threshold in thresholds:
            threshold.last_triggered_at = None
            threshold.last_triggered_cycle_start = None
        await self.store.flush()
        await self.store.refresh(settings)
        await self._sync_litellm_budgets(org_id, settings, overrides)
        return True

    async def _fetch_budget_financial_data(self, org_id: UUID) -> dict | None:
        try:
            return await LiteLlmManager.get_team_members_financial_data(str(org_id))
        except Exception as e:
            logger.warning(
                'org_budget_litellm_financial_data_fetch_failed',
                extra={'org_id': str(org_id), 'error': str(e)},
            )
            return None

    async def _fetch_team_spend(self, org_id: UUID) -> float:
        financial_data = await self._fetch_budget_financial_data(org_id)
        if not financial_data:
            return 0.0
        return _float_or_zero(financial_data.get('team_spend'))

    async def _get_cycle_spend(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        financial_data: dict | None = None,
    ) -> float:
        if financial_data is None:
            financial_data = await self._fetch_budget_financial_data(org_id)
        return _litellm_cycle_spend(settings, financial_data)

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

        if status_value == 'disabled':
            return is_disabled
        if status_value == 'nocap':
            return not is_disabled and (effective_limit is None or effective_limit <= 0)
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
        financial_data: dict | None,
        users_page: int,
        users_per_page: int,
        users_search: str | None,
        users_status: str | None,
    ) -> tuple[list[dict], int]:
        if financial_data is None:
            financial_data = await self._fetch_budget_financial_data(org_id)
        members = (financial_data or {}).get('members', {})

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
            escaped = _escape_ilike(search_value)
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
        settings = await self._get_or_create_settings(org_id)
        overrides = await self._get_overrides(org_id)
        financial_data = await self._fetch_budget_financial_data(org_id)
        members = (financial_data or {}).get('members', {})

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
        return {
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

    async def _sync_litellm_budgets(
        self,
        org_id: UUID,
        settings: OrgBudgetSettings,
        overrides: list[OrgUserBudgetOverride],
        clear_disabled: bool = False,
        financial_data: dict | None = None,
    ) -> dict | None:
        if not settings.enabled and not clear_disabled:
            await self._record_litellm_sync(settings, 'skipped')
            return financial_data

        sync_errors: list[str] = []
        if financial_data is None:
            try:
                financial_data = await LiteLlmManager.get_team_members_financial_data(
                    str(org_id)
                )
            except Exception as e:
                error_message = f'fetch_failed: {e}'
                logger.warning(
                    'org_budget_litellm_fetch_failed',
                    extra={'org_id': str(org_id), 'error': str(e)},
                )
                await self._record_litellm_sync(
                    settings, 'error', error_message[:500]
                )
                return None

        if not financial_data:
            await self._record_litellm_sync(settings, 'skipped')
            return financial_data

        members = financial_data.get('members', {})

        if settings.enabled and settings.monthly_limit:
            # Anchor LiteLLM budgets to our cycle start spend so resets stay aligned.
            max_budget = settings.cycle_start_spend + settings.monthly_limit
            try:
                await LiteLlmManager.update_team(
                    str(org_id),
                    team_alias=None,
                    max_budget=max_budget,
                )
            except Exception as e:
                sync_errors.append(f'team_update_failed: {e}')
                logger.warning(
                    'org_budget_litellm_team_update_failed',
                    extra={'org_id': str(org_id), 'error': str(e)},
                )
        else:
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

        for user_id, info in members.items():
            baseline = existing_user_baselines.get(user_id)
            if baseline is None:
                baseline = _float_or_zero(info.get('spend'))
            active_user_baselines[user_id] = baseline

            override = override_map.get(user_id)
            effective_limit, is_disabled, _ = _effective_user_budget_limit(
                override, settings.default_user_monthly_limit
            )
            if is_disabled:
                max_budget_in_team = None
                clear_budget = True
            elif effective_limit is not None:
                # LiteLLM compares cumulative spend against an absolute member cap.
                max_budget_in_team = baseline + effective_limit
                clear_budget = False
            else:
                max_budget_in_team = None
                clear_budget = True
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

        settings.user_cycle_start_spend = active_user_baselines

        if sync_errors:
            summary = sync_errors[0]
            if len(sync_errors) > 1:
                summary = f'{summary} (+{len(sync_errors) - 1} more)'
            await self._record_litellm_sync(settings, 'error', summary[:500])
        else:
            await self._record_litellm_sync(settings, 'success')
        return financial_data

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

            await self._send_alerts(
                org_id,
                settings,
                threshold,
                current_spend,
                percentage,
            )
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
    ) -> None:
        org_name = await self._get_org_name(org_id)
        if threshold.email_enabled:
            recipients = await self._get_admin_emails(org_id)
            if recipients:
                await asyncio.to_thread(
                    SMTPEmailService.send_budget_alert_email,
                    recipients,
                    org_name=org_name,
                    percentage=percentage,
                    current_spend=current_spend,
                    monthly_limit=settings.monthly_limit or 0,
                    threshold=threshold.percentage,
                )

        if threshold.slack_enabled:
            await self._send_slack_alert(
                org_name,
                settings,
                threshold.percentage,
                current_spend,
                percentage,
            )

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
    ) -> None:
        if not SLACK_AVAILABLE:
            logger.warning('Slack SDK not installed, skipping slack budget alert')
            return
        if not settings.slack_channel:
            return
        team_id = await self._resolve_slack_team_id(settings)
        if not team_id:
            return
        result = await self.db_session.execute(
            select(SlackTeam.bot_access_token).where(SlackTeam.team_id == team_id)
        )
        token = result.scalar_one_or_none()
        if not token:
            return

        client = AsyncWebClient(token=token)
        message = (
            f':warning: OpenHands budget alert for *{org_name}*\n'
            f'Threshold: *{threshold}%*\n'
            f'Current spend: *${current_spend:,.2f}* '
            f'({percentage:.1f}% of ${settings.monthly_limit:,.2f})'
        )
        try:
            await client.chat_postMessage(
                channel=settings.slack_channel,
                text=message,
            )
        except Exception as e:
            logger.warning(
                'Slack budget alert failed',
                extra={'error': str(e), 'team_id': team_id},
            )

    async def _resolve_slack_team_id(self, settings: OrgBudgetSettings) -> str | None:
        if settings.slack_team_id:
            return settings.slack_team_id
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
