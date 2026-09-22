from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import OrgBudgetService
from storage.org_budget_settings import OrgBudgetSettings


@pytest.fixture
def alert_service(monkeypatch):
    for name in (
        'SMTP_HOST',
        'RESEND_API_KEY',
        'SLACK_WEBHOOKS_ENABLED',
        'SLACK_CLIENT_ID',
        'SLACK_CLIENT_SECRET',
        'SLACK_SIGNING_SECRET',
    ):
        monkeypatch.delenv(name, raising=False)
    return OrgBudgetService(AsyncMock())


def enable_slack(monkeypatch, flag='true'):
    monkeypatch.setenv('SLACK_WEBHOOKS_ENABLED', flag)
    for name in ('SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET', 'SLACK_SIGNING_SECRET'):
        monkeypatch.setenv(name, 'test-only')


@pytest.mark.asyncio
async def test_resend_does_not_enable_smtp_budget_alerts(alert_service, monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY', 'test-only')
    settings = OrgBudgetSettings(org_id=uuid4())
    availability = await alert_service._alert_availability(settings)
    assert not any(availability.values())
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.invalid')
    assert (await alert_service._alert_availability(settings))['email_alerts_available']


@pytest.mark.asyncio
@pytest.mark.parametrize('flag', ['true', '1'])
async def test_slack_requires_deployment_and_connection(
    alert_service, monkeypatch, flag
):
    settings = OrgBudgetSettings(org_id=uuid4(), slack_team_id='T_TEST')
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    alert_service.db_session.execute.return_value = result
    enable_slack(monkeypatch, flag)
    availability = await alert_service._alert_availability(settings)
    assert availability['slack_alerts_enabled']
    assert not availability['slack_alerts_available']
    result.scalar_one_or_none.return_value = 'test-only-token'
    assert (await alert_service._alert_availability(settings))['slack_alerts_available']
    monkeypatch.setenv('SLACK_WEBHOOKS_ENABLED', 'false')
    assert not (await alert_service._alert_availability(settings))[
        'slack_alerts_available'
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('channel', ['email', 'slack'])
async def test_reject_unavailable_channel_activation(alert_service, channel):
    settings = OrgBudgetSettings(org_id=uuid4())
    update = OrgBudgetSettingsUpdate(
        thresholds=[
            {
                'percentage': 80,
                'email_enabled': channel == 'email',
                'slack_enabled': channel == 'slack',
            }
        ]
    )
    with pytest.raises(HTTPException) as error:
        await alert_service._validate_alert_settings(settings, update)
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_allow_removing_invalid_alert_settings(alert_service):
    settings = OrgBudgetSettings(org_id=uuid4(), slack_team_id='removed')
    for update in (
        OrgBudgetSettingsUpdate(monthly_limit=100),
        OrgBudgetSettingsUpdate(thresholds=[]),
        OrgBudgetSettingsUpdate(
            thresholds=[
                {
                    'percentage': 80,
                    'email_enabled': False,
                    'slack_enabled': False,
                }
            ]
        ),
    ):
        await alert_service._validate_alert_settings(settings, update)


@pytest.mark.asyncio
async def test_slack_activation_requires_destination(alert_service):
    settings = OrgBudgetSettings(org_id=uuid4(), slack_team_id='T_TEST')
    alert_service._get_slack_bot_token = AsyncMock(return_value='test-only-token')
    update = OrgBudgetSettingsUpdate(
        thresholds=[
            {
                'percentage': 80,
                'email_enabled': False,
                'slack_enabled': True,
            }
        ]
    )
    with pytest.raises(HTTPException, match='Select a Slack channel'):
        await alert_service._validate_alert_settings(settings, update)
    update.slack_channel = '#budget-alerts'
    await alert_service._validate_alert_settings(settings, update)


@pytest.mark.asyncio
async def test_disabled_slack_never_sends_existing_alert(alert_service):
    settings = OrgBudgetSettings(
        org_id=uuid4(), slack_team_id='T_TEST', slack_channel='#alerts'
    )
    with patch('server.services.org_budget_service.AsyncWebClient') as client:
        await alert_service._send_slack_alert('Test', settings, 80, 80, 80)
    client.assert_not_called()
    alert_service.db_session.execute.assert_not_awaited()
