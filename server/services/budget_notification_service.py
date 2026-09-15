"""Versioned alert preferences, independent of native financial policy."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from server.services.budget_adoption_plan import BudgetAdoptionUnsupported
from server.services.budget_constants import DEFAULT_THRESHOLDS
from server.services.smtp_email_service import SMTPEmailService
from storage.budget_control import (
    BudgetControlConflict,
    budget_control_session,
    budget_request_hash,
)
from storage.org import Org
from storage.org_budget_store import OrgBudgetStore
from storage.slack_team import SlackTeam
from storage.slack_user import SlackUser
from storage.user import User


class BudgetNotificationThreshold(BaseModel):
    model_config = ConfigDict(extra='forbid')

    percentage: int = Field(gt=0, le=100, strict=True)
    email_enabled: bool = Field(strict=True)
    slack_enabled: bool = Field(strict=True)


class BudgetNotificationPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')

    thresholds: list[BudgetNotificationThreshold] = Field(max_length=100)
    slack_channel: str | None = Field(
        default=None, max_length=80, pattern=r'^#[a-z0-9][a-z0-9_-]*$'
    )
    slack_team_id: str | None = Field(
        default=None, max_length=80, pattern=r'^T[A-Z0-9]+$'
    )

    @model_validator(mode='after')
    def unique_thresholds(self) -> 'BudgetNotificationPreferences':
        if len({row.percentage for row in self.thresholds}) != len(self.thresholds):
            raise ValueError('Threshold percentages must be unique')
        self.thresholds.sort(key=lambda row: row.percentage)
        return self


class BudgetNotificationUpdate(BudgetNotificationPreferences):
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class BudgetNotificationState(BudgetNotificationPreferences):
    fingerprint: str
    email_configured: bool
    slack_account_linked: bool


class BudgetNotificationService:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def _read(
        self, session: AsyncSession, org_id: UUID, actor: str
    ) -> BudgetNotificationState:
        if await session.get(Org, org_id) is None:
            raise HTTPException(404, 'Organization not found')
        if await session.scalar(select(User.id).where(User.id == org_id)):
            raise HTTPException(
                400, 'Budgets are not available for personal workspaces'
            )
        store = OrgBudgetStore(session)
        settings = await store.get_settings(org_id)
        rows = await store.get_thresholds(org_id)
        preferences = BudgetNotificationPreferences(
            thresholds=[
                BudgetNotificationThreshold(
                    percentage=row.percentage,
                    email_enabled=row.email_enabled,
                    slack_enabled=row.slack_enabled,
                )
                for row in rows
            ]
            if settings is not None
            else [
                BudgetNotificationThreshold(
                    percentage=percentage, email_enabled=email, slack_enabled=slack
                )
                for percentage, email, slack in DEFAULT_THRESHOLDS
            ],
            slack_channel=settings.slack_channel if settings else None,
            slack_team_id=settings.slack_team_id if settings else None,
        )
        linked = await session.scalar(
            select(SlackUser.id)
            .where(SlackUser.org_id == org_id, SlackUser.keycloak_user_id == actor)
            .limit(1)
        )
        return BudgetNotificationState(
            **preferences.model_dump(),
            fingerprint=budget_request_hash(
                {'org_id': str(org_id), **preferences.model_dump()}
            ),
            email_configured=SMTPEmailService.is_configured(),
            slack_account_linked=linked is not None,
        )

    async def get(self, org_id: UUID, actor: str) -> BudgetNotificationState:
        async with AsyncSession(self.engine) as session:
            return await self._read(session, org_id, actor)

    async def update(
        self, org_id: UUID, actor: str, request: BudgetNotificationUpdate
    ) -> BudgetNotificationState:
        async with budget_control_session(self.engine, org_id) as control:
            session = control.session
            current = await self._read(session, org_id, actor)
            target = request.model_dump(exclude={'expected_fingerprint'})
            previous = current.model_dump(
                exclude={'fingerprint', 'email_configured', 'slack_account_linked'}
            )
            if target == previous:
                return current
            if current.fingerprint != request.expected_fingerprint:
                raise BudgetControlConflict(
                    'Alert preferences changed. Refresh before saving.'
                )
            if any(row.slack_enabled for row in request.thresholds):
                await self._verify_slack_destination(session, org_id, actor, request)
            store = OrgBudgetStore(session)
            settings = await store.get_settings(org_id)
            if settings is None:
                now = datetime.now(UTC)
                settings = await store.create_settings(
                    org_id,
                    1,
                    now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                    (),
                )
            settings.slack_channel = request.slack_channel
            settings.slack_team_id = request.slack_team_id
            await store.replace_thresholds(
                org_id, await store.get_thresholds(org_id), request.thresholds
            )
            await session.commit()
            return await self._read(session, org_id, actor)

    async def _verify_slack_destination(
        self,
        session: AsyncSession,
        org_id: UUID,
        actor: str,
        request: BudgetNotificationUpdate,
    ) -> None:
        if not request.slack_channel or not request.slack_team_id:
            raise BudgetAdoptionUnsupported(
                'Choose a Slack workspace and channel before enabling Slack alerts.'
            )
        user_ids = list(
            await session.scalars(
                select(SlackUser.slack_user_id).where(
                    SlackUser.org_id == org_id, SlackUser.keycloak_user_id == actor
                )
            )
        )
        token = await session.scalar(
            select(SlackTeam.bot_access_token).where(
                SlackTeam.team_id == request.slack_team_id
            )
        )
        if not user_ids or not token:
            raise BudgetAdoptionUnsupported(
                'Link your Slack account in this organization to the chosen workspace first.'
            )
        client = AsyncWebClient(token=token, timeout=5, retry_handlers=[])
        async with asyncio.timeout(10):
            auth = await client.auth_test()
            if auth.get('ok') is True and auth.get('team_id') == request.slack_team_id:
                for user_id in user_ids:
                    try:
                        result = await client.users_info(user=user_id)
                    except SlackApiError as error:
                        if error.response.get('error') == 'user_not_found':
                            continue
                        raise
                    user = result.get('user', {})
                    if (
                        result.get('ok') is True
                        and user.get('id') == user_id
                        and user.get('team_id') == request.slack_team_id
                        and user.get('deleted') is False
                        and user.get('is_bot') is False
                    ):
                        return
        raise BudgetAdoptionUnsupported(
            'Your linked Slack account does not belong to the chosen workspace.'
        )
