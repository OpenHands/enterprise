from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import OrgBudgetService
from storage.org_budget_settings import OrgBudgetSettings
from storage.slack_team import SlackTeam


@pytest.fixture
async def alert_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def alert_service(monkeypatch, alert_session):
    for name in (
        'SMTP_HOST',
        'RESEND_API_KEY',
        'SLACK_WEBHOOKS_ENABLED',
        'SLACK_CLIENT_ID',
        'SLACK_CLIENT_SECRET',
        'SLACK_SIGNING_SECRET',
    ):
        monkeypatch.delenv(name, raising=False)
    return OrgBudgetService(alert_session)


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
    enable_slack(monkeypatch, flag)
    availability = await alert_service._alert_availability(settings)
    assert availability['slack_integration_configured']
    assert not availability['slack_workspace_connected']
    alert_service.db_session.add(
        SlackTeam(team_id='T_TEST', bot_access_token='test-only-token')
    )
    await alert_service.db_session.flush()
    assert (await alert_service._alert_availability(settings))[
        'slack_workspace_connected'
    ]
    monkeypatch.setenv('SLACK_WEBHOOKS_ENABLED', 'false')
    assert not (await alert_service._alert_availability(settings))[
        'slack_workspace_connected'
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
        await alert_service._validate_alert_settings(settings, update, [])
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
        await alert_service._validate_alert_settings(settings, update, [])


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
        await alert_service._validate_alert_settings(settings, update, [])
    update.slack_channel = '#budget-alerts'
    await alert_service._validate_alert_settings(settings, update, [])


@pytest.mark.asyncio
async def test_disabled_slack_never_sends_existing_alert(alert_service):
    settings = OrgBudgetSettings(
        org_id=uuid4(), slack_team_id='T_TEST', slack_channel='#alerts'
    )
    with patch('server.services.org_budget_service.AsyncWebClient') as client:
        await alert_service._send_slack_alert('Test', settings, 80, 80, 80)
    client.assert_not_called()


@pytest.fixture
async def alert_http(alert_service, create_org):
    import httpx
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    from server.routes.orgs import _org_budget_service_injector, org_router
    from storage.lite_llm_manager import LiteLlmManager
    from storage.org_budget_threshold import OrgBudgetThreshold

    org = create_org()
    alert_service.db_session.add(OrgBudgetSettings(org_id=org.id, monthly_limit=100))
    alert_service.db_session.add(
        OrgBudgetThreshold(
            org_id=org.id, percentage=80, email_enabled=False, slack_enabled=False
        )
    )
    await alert_service.db_session.commit()
    app = FastAPI()
    app.include_router(org_router)

    async def service():
        yield alert_service
        await alert_service.db_session.commit()

    async def identity():
        return str(uuid4())

    async def context():
        return None

    app.dependency_overrides[_org_budget_service_injector.depends] = service
    for route in app.routes:
        if isinstance(route, APIRoute) and '/budgets' in route.path:
            for dependency in route.dependant.dependencies:
                if dependency.call and dependency.name == 'user_id':
                    app.dependency_overrides[dependency.call] = identity
                elif dependency.call and dependency.name is None:
                    app.dependency_overrides[dependency.call] = context
    with patch.object(
        LiteLlmManager,
        'get_team_members_financial_data',
        AsyncMock(
            return_value={'team_spend': 0, 'team_max_budget': None, 'members': {}}
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        ) as client:
            yield client, org.id


@pytest.mark.asyncio
@pytest.mark.parametrize('channel', ['email', 'slack'])
async def test_api_rejects_new_unavailable_alert_before_replacing_settings(
    alert_http, alert_service, channel
):
    from sqlalchemy import select

    from storage.org_budget_threshold import OrgBudgetThreshold

    client, org_id = alert_http
    with patch('server.services.org_budget_service.quint_oracle.log') as oracle:
        response = await client.patch(
            f'/api/organizations/{org_id}/budgets',
            json={
                'monthly_limit': 150,
                'thresholds': [
                    {
                        'percentage': 80,
                        'email_enabled': channel == 'email',
                        'slack_enabled': channel == 'slack',
                    }
                ],
            },
        )
    assert response.status_code == 400, response.text
    assert channel.title() in response.json()['detail']
    assert any(
        call.kwargs.get('outcome') == 'rejected' for call in oracle.call_args_list
    )
    settings = (
        await alert_service.db_session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )
    ).scalar_one()
    threshold = (
        await alert_service.db_session.execute(
            select(OrgBudgetThreshold).where(OrgBudgetThreshold.org_id == org_id)
        )
    ).scalar_one()
    assert settings.monthly_limit == 100
    assert not threshold.email_enabled and not threshold.slack_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize('channel', ['email', 'slack'])
async def test_api_preserves_stored_flags_during_channel_outage(
    alert_http, alert_service, channel
):
    from sqlalchemy import select

    from storage.org_budget_threshold import OrgBudgetThreshold

    client, org_id = alert_http
    threshold = (
        await alert_service.db_session.execute(
            select(OrgBudgetThreshold).where(OrgBudgetThreshold.org_id == org_id)
        )
    ).scalar_one()
    setattr(threshold, f'{channel}_enabled', True)
    await alert_service.db_session.commit()
    response = await client.patch(
        f'/api/organizations/{org_id}/budgets',
        json={
            'monthly_limit': 150,
            'thresholds': [
                {
                    'percentage': 80,
                    'email_enabled': channel == 'email',
                    'slack_enabled': channel == 'slack',
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()['monthly_limit'] == 150
    assert response.json()['thresholds'][0][f'{channel}_enabled'] is True
    # Moving the enabled flag to a new percentage is a new activation.
    response = await client.patch(
        f'/api/organizations/{org_id}/budgets',
        json={
            'thresholds': [
                {
                    'percentage': 90,
                    'email_enabled': channel == 'email',
                    'slack_enabled': channel == 'slack',
                }
            ],
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_budget_api_reports_instance_configuration_and_workspace(
    alert_http, alert_service, monkeypatch
):
    client, org_id = alert_http
    endpoint = f'/api/organizations/{org_id}/budgets'
    response = await client.get(endpoint)
    assert response.status_code == 200, response.text
    fields = [
        'email_alerts_available',
        'slack_integration_configured',
        'slack_workspace_connected',
    ]
    assert {name: response.json()[name] for name in fields} == dict.fromkeys(
        fields, False
    )
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.invalid')
    enable_slack(monkeypatch)
    response = await client.get(endpoint)
    assert response.json()['email_alerts_available'] is True
    assert response.json()['slack_integration_configured'] is True
    assert response.json()['slack_workspace_connected'] is False
    alert_service.db_session.add(
        SlackTeam(team_id='T_ONLY', bot_access_token='test-only')
    )
    await alert_service.db_session.commit()
    assert (await client.get(endpoint)).json()['slack_workspace_connected'] is True
    alert_service.db_session.add(
        SlackTeam(team_id='T_OTHER', bot_access_token='test-only')
    )
    await alert_service.db_session.commit()
    assert (await client.get(endpoint)).json()['slack_workspace_connected'] is False


@pytest.mark.asyncio
async def test_slack_failure_log_keeps_resolved_single_workspace(
    alert_service, monkeypatch
):
    enable_slack(monkeypatch)
    alert_service.db_session.add(
        SlackTeam(team_id='T_ONLY', bot_access_token='test-only')
    )
    await alert_service.db_session.flush()
    settings = OrgBudgetSettings(
        org_id=uuid4(), slack_channel='#alerts', monthly_limit=100
    )
    with (
        patch('server.services.org_budget_service.AsyncWebClient') as client,
        patch('server.services.org_budget_service.logger.warning') as warning,
    ):
        client.return_value.chat_postMessage = AsyncMock(
            side_effect=RuntimeError('test failure')
        )
        await alert_service._send_slack_alert('Test', settings, 80, 80, 80)
    assert warning.call_args.kwargs['extra']['team_id'] == 'T_ONLY'
