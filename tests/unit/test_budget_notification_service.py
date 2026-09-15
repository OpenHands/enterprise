"""Alert preferences cannot grant financial authority or cross Slack tenants."""

import asyncio
from contextvars import Context
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import inspect, select

from server.services.budget_adoption_plan import BudgetAdoptionUnsupported
from server.services.budget_notification_service import (
    BudgetNotificationService,
    BudgetNotificationUpdate,
)
from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    budget_control_session,
)
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.slack_team import SlackTeam
from storage.slack_user import SlackUser
from tests.unit.test_budget_adoption_service import adoption as adoption_fixture

adoption = adoption_fixture


@pytest.fixture
async def notifications(create_org, create_user, async_engine, async_session_maker):
    org = create_org()
    actor = create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        session.add(
            OrgBudgetSettings(
                org_id=org.id,
                enabled=True,
                control_mode='needs_adoption',
                monthly_limit=1,
                cycle_start_spend=589.69,
                user_cycle_start_spend={str(actor.id): 22},
            )
        )
        session.add(
            OrgBudgetThreshold(
                org_id=org.id,
                percentage=80,
                email_enabled=True,
                slack_enabled=False,
                last_triggered_cycle_start=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    with (
        patch(
            'storage.lite_llm_manager.LiteLlmManager.apply_budget_write',
            AsyncMock(side_effect=AssertionError('No native writes for alerts')),
        ),
        patch(
            'storage.lite_llm_manager.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(side_effect=AssertionError('No native dependency for alerts')),
        ),
    ):
        yield org.id, str(actor.id), BudgetNotificationService(async_engine)


def update(state, **changes):
    body = state.model_dump(
        exclude={'fingerprint', 'email_configured', 'slack_account_linked'}
    )
    return BudgetNotificationUpdate(
        **(body | changes), expected_fingerprint=state.fingerprint
    )


async def financial_state(session, org_id):
    settings = await session.scalar(
        select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
    )
    return {
        field.key: getattr(settings, field.key)
        for field in inspect(OrgBudgetSettings).column_attrs
        if field.key not in {'slack_channel', 'slack_team_id', 'updated_at'}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['external', 'needs_adoption', 'managed'])
async def test_preference_save_preserves_financial_state_and_deduplication(
    notifications, async_session_maker, mode
):
    org_id, actor, service = notifications
    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )
        settings.control_mode = mode
        await session.commit()
        before = await financial_state(session, org_id)
    initial = await service.get(org_id, actor)
    request = update(
        initial,
        thresholds=[
            {'percentage': 80, 'email_enabled': False, 'slack_enabled': False},
            {'percentage': 95, 'email_enabled': True, 'slack_enabled': False},
        ],
    )
    saved = await service.update(org_id, actor, request)
    assert saved.fingerprint != initial.fingerprint
    assert [row.percentage for row in saved.thresholds] == [80, 95]
    assert await service.update(org_id, actor, request) == saved
    async with async_session_maker() as session:
        assert await financial_state(session, org_id) == before
        threshold = await session.scalar(
            select(OrgBudgetThreshold).where(
                OrgBudgetThreshold.org_id == org_id,
                OrgBudgetThreshold.percentage == 80,
            )
        )
        assert threshold.last_triggered_cycle_start == datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_stale_preferences_cannot_overwrite_newer_edit(notifications):
    org_id, actor, service = notifications
    initial = await service.get(org_id, actor)
    saved = await service.update(org_id, actor, update(initial, thresholds=[]))
    with pytest.raises(BudgetControlConflict, match='Alert preferences changed'):
        await service.update(org_id, actor, update(initial, slack_channel='#other'))
    assert await service.get(org_id, actor) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize('inherit_context', [False, True])
async def test_preference_save_uses_same_cross_task_org_lock(
    notifications, inherit_context
):
    org_id, actor, service = notifications
    initial = await service.get(org_id, actor)
    async with budget_control_session(service.engine, org_id):
        with pytest.raises(
            BudgetWriteDenied if inherit_context else BudgetControlConflict
        ):
            await asyncio.create_task(
                service.update(org_id, actor, update(initial, thresholds=[])),
                context=None if inherit_context else Context(),
            )


@pytest.mark.asyncio
async def test_response_loss_after_commit_replays_without_changing_financial_state(
    notifications, async_session_maker
):
    org_id, actor, service = notifications
    initial = await service.get(org_id, actor)
    request = update(initial, thresholds=[])
    async with async_session_maker() as session:
        before = await financial_state(session, org_id)
    with patch.object(
        service,
        '_read',
        AsyncMock(side_effect=[initial, RuntimeError('response lost')]),
    ):
        with pytest.raises(RuntimeError, match='response lost'):
            await service.update(org_id, actor, request)
    result = await service.update(org_id, actor, request)
    assert result.thresholds == []
    assert result.fingerprint != initial.fingerprint
    async with async_session_maker() as session:
        assert await financial_state(session, org_id) == before


@pytest.mark.asyncio
async def test_alert_save_does_not_replace_pending_adoption(
    adoption, async_session_maker
):
    org_id, actor, controller, request, proxy = adoption
    proxy.lose_next_response = True
    operation = await controller.confirm(org_id, actor, request)
    assert operation['status'] == 'pending'
    writes = list(proxy.writes)
    observations = proxy.observations
    service = BudgetNotificationService(controller.engine)
    initial = await service.get(org_id, actor)
    async with async_session_maker() as session:
        before = await financial_state(session, org_id)
    await service.update(org_id, actor, update(initial, thresholds=[]))
    async with async_session_maker() as session:
        assert await financial_state(session, org_id) == before
    assert proxy.writes == writes
    assert proxy.observations == observations
    assert (await controller.retry(org_id))['operation_id'] == operation['operation_id']


@pytest.mark.asyncio
async def test_new_preferences_initialize_external_without_touching_proxy(
    create_org, create_user, async_engine, async_session_maker
):
    org = create_org()
    actor = create_user(current_org_id=org.id)
    service = BudgetNotificationService(async_engine)
    initial = await service.get(org.id, str(actor.id))
    async with async_session_maker() as session:
        assert (
            await session.scalar(
                select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org.id)
            )
            is None
        )
    await service.update(org.id, str(actor.id), update(initial, thresholds=[]))
    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org.id)
        )
        assert settings.control_mode == 'external'
        assert settings.control_generation == 0
        assert settings.enabled is False


@pytest.mark.parametrize(
    'changes',
    [
        {'monthly_limit': 1000},
        {'enabled': True},
        {'expected_fingerprint': 'invalid'},
        {
            'thresholds': [
                {'percentage': 0, 'email_enabled': True, 'slack_enabled': False}
            ]
        },
        {
            'thresholds': [
                {'percentage': True, 'email_enabled': True, 'slack_enabled': False}
            ]
        },
        {
            'thresholds': [
                {'percentage': 80, 'email_enabled': 'false', 'slack_enabled': False}
            ]
        },
        {
            'thresholds': [
                {
                    'percentage': 80,
                    'email_enabled': True,
                    'slack_enabled': False,
                    'budget': 0,
                }
            ]
        },
        {
            'thresholds': [
                {'percentage': 80, 'email_enabled': True, 'slack_enabled': False}
            ]
            * 2
        },
        {'slack_channel': '#channel\n@everyone'},
        {'slack_team_id': 'https://other'},
    ],
)
def test_strict_preference_payload(changes):
    with pytest.raises(ValidationError):
        BudgetNotificationUpdate.model_validate(
            {'expected_fingerprint': 'a' * 64, 'thresholds': []} | changes
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', ['valid', 'other_org', 'other_actor', 'none'])
@pytest.mark.parametrize('workspace', ['same', 'foreign', 'deleted', 'wrong_token'])
async def test_slack_requires_linked_actor_and_verified_workspace(
    notifications, async_session_maker, create_org, binding, workspace
):
    org_id, actor, service = notifications
    other_org = create_org()
    async with async_session_maker() as session:
        session.add(SlackTeam(team_id='T123', bot_access_token='private-token'))
        if binding != 'none':
            session.add(
                SlackUser(
                    org_id=other_org.id if binding == 'other_org' else org_id,
                    keycloak_user_id='other' if binding == 'other_actor' else actor,
                    slack_user_id='U123',
                    slack_display_name='Test',
                )
            )
        await session.commit()
    initial = await service.get(org_id, actor)
    assert initial.slack_account_linked == (binding == 'valid')
    request = update(
        initial,
        slack_channel='#budget-alerts',
        slack_team_id='T123',
        thresholds=[{'percentage': 80, 'email_enabled': False, 'slack_enabled': True}],
    )
    client = AsyncMock()
    client.auth_test.return_value = {
        'ok': True,
        'team_id': 'T999' if workspace == 'wrong_token' else 'T123',
    }
    client.users_info.return_value = {
        'ok': True,
        'user': {
            'id': 'U123',
            'team_id': 'T999' if workspace == 'foreign' else 'T123',
            'deleted': workspace == 'deleted',
            'is_bot': False,
        },
    }
    with patch(
        'server.services.budget_notification_service.AsyncWebClient',
        return_value=client,
    ):
        if binding == 'valid' and workspace == 'same':
            saved = await service.update(org_id, actor, request)
            assert saved.slack_team_id == 'T123'
            assert saved.thresholds[0].slack_enabled
            assert 'private-token' not in saved.model_dump_json()
        else:
            with pytest.raises(BudgetAdoptionUnsupported):
                await service.update(org_id, actor, request)
            assert await service.get(org_id, actor) == initial
        client.chat_postMessage.assert_not_called()
        if binding != 'valid':
            client.auth_test.assert_not_called()


@pytest.mark.asyncio
async def test_personal_workspace_cannot_configure_alerts(
    create_org, create_user, async_engine
):
    actor = create_user()
    org = create_org(id=actor.id)
    service = BudgetNotificationService(async_engine)
    with pytest.raises(HTTPException) as error:
        await service.get(org.id, str(actor.id))
    assert error.value.status_code == 400
