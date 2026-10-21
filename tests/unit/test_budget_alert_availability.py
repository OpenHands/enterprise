from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import OrgBudgetService
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.role import Role
from storage.slack_team import SlackTeam
from storage.slack_user import SlackUser


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


@pytest.fixture
def link_slack(alert_service, create_user):
    """Make a new org member and link Slack for them, like the OAuth callback.

    ``create_user`` gives each user a personal current org, so the link's
    ``org_id`` is never the org under test.
    """
    session = alert_service.db_session
    role = Role(name='member', rank=1000)

    async def _link(org_id, team_id: str) -> SlackUser:
        if role.id is None:
            session.add(role)
            await session.flush()
        user = create_user()
        link = SlackUser(
            keycloak_user_id=str(user.id),
            org_id=user.current_org_id,
            slack_user_id=f'U{uuid4().hex[:8]}',
            slack_display_name='Test User',
            team_id=team_id,
        )
        session.add_all(
            [
                OrgMember(
                    org_id=org_id,
                    user_id=user.id,
                    role_id=role.id,
                    llm_api_key='test-only',
                ),
                link,
            ]
        )
        await session.flush()
        return link

    return _link


def install_slack_teams(alert_service, *team_ids: str) -> None:
    alert_service.db_session.add_all(
        SlackTeam(team_id=team_id, bot_access_token=f'{team_id}-token')
        for team_id in team_ids
    )


@pytest.mark.asyncio
async def test_resolves_member_workspace_linked_from_another_org(
    alert_service, monkeypatch, create_org, link_slack
):
    enable_slack(monkeypatch)
    org_id = create_org().id
    install_slack_teams(alert_service, 'T_OTHER', 'T_TARGET')
    link = await link_slack(org_id, 'T_TARGET')
    assert link.org_id != org_id

    team_id = await alert_service._resolve_slack_team_id(None, org_id)

    assert team_id == 'T_TARGET'


@pytest.mark.asyncio
async def test_does_not_resolve_when_members_linked_several_workspaces(
    alert_service, monkeypatch, create_org, link_slack
):
    enable_slack(monkeypatch)
    org_id = create_org().id
    install_slack_teams(alert_service, 'T_ONE', 'T_TWO')
    await link_slack(org_id, 'T_ONE')
    await link_slack(org_id, 'T_TWO')

    team_id = await alert_service._resolve_slack_team_id(None, org_id)

    assert team_id is None


@pytest.mark.asyncio
async def test_save_accepts_slack_alert_with_multiple_installs(
    alert_service, monkeypatch, create_org, link_slack
):
    enable_slack(monkeypatch)
    org_id = create_org().id
    install_slack_teams(alert_service, 'T_OTHER', 'T_TARGET')
    await link_slack(org_id, 'T_TARGET')
    settings = OrgBudgetSettings(org_id=org_id)
    update = OrgBudgetSettingsUpdate(
        slack_channel='#on-callstatus',
        thresholds=[
            {
                'percentage': 80,
                'email_enabled': False,
                'slack_enabled': True,
            }
        ],
    )

    team_id = await alert_service._validate_alert_settings(settings, update, [])

    assert team_id == 'T_TARGET'
    availability = await alert_service._alert_availability(settings)
    assert availability['slack_workspace_connected']


@pytest.mark.asyncio
async def test_slack_alert_uses_saved_workspace_after_new_member_links(
    alert_http, alert_service, monkeypatch, link_slack
):
    enable_slack(monkeypatch)
    http, org_id = alert_http
    install_slack_teams(alert_service, 'T_OTHER', 'T_TARGET')
    link = await link_slack(org_id, 'T_TARGET')
    await alert_service.db_session.commit()
    response = await save_slack_alert(http, org_id, [link], '#on-callstatus')
    assert response.status_code == 200, response.text
    # A member links another workspace after the admin saved.
    await link_slack(org_id, 'T_OTHER')
    settings = await stored_settings(alert_service, org_id)
    client = AsyncMock()
    client.chat_postMessage.return_value = {'ok': True}

    with patch(
        'server.services.org_budget_service.AsyncWebClient', return_value=client
    ) as client_factory:
        delivered = await alert_service._send_slack_alert(
            'Test organization', settings, 80, 85, 85
        )

    assert delivered
    client_factory.assert_called_once_with(token='T_TARGET-token')
    client.chat_postMessage.assert_awaited_once_with(
        channel='#on-callstatus',
        text=(
            ':warning: OpenHands budget alert for *Test organization*\n'
            'Threshold: *80%*\n'
            'Current spend: *$85.00* (85.0% of $100.00)'
        ),
    )


@pytest.mark.asyncio
async def test_slack_alert_never_resolves_workspace_from_member_links(
    alert_service, monkeypatch, create_org, link_slack
):
    enable_slack(monkeypatch)
    org_id = create_org().id
    install_slack_teams(alert_service, 'T_OTHER', 'T_MEMBER')
    await link_slack(org_id, 'T_MEMBER')
    settings = OrgBudgetSettings(
        org_id=org_id, monthly_limit=100, slack_channel='#on-callstatus'
    )

    with patch('server.services.org_budget_service.AsyncWebClient') as client:
        delivered = await alert_service._send_slack_alert('Test', settings, 80, 85, 85)

    assert not delivered
    client.assert_not_called()


async def save_slack_alert(client, org_id, links, channel: str):
    """PATCH the budget with a Slack alert, as the org-budget Save does.

    The linked users are org members, so LiteLLM must report them too.
    """
    from storage.lite_llm_manager import LiteLlmManager

    member = {'spend': 0.0, 'max_budget': None, 'uses_shared_budget': True}
    financial_data = {
        'team_spend': 0,
        'team_max_budget': None,
        'members': {link.keycloak_user_id: member for link in links},
    }
    with patch.object(
        LiteLlmManager,
        'get_team_members_financial_data',
        AsyncMock(return_value=financial_data),
    ):
        return await client.patch(
            f'/api/organizations/{org_id}/budgets',
            json={
                'slack_channel': channel,
                'thresholds': [
                    {'percentage': 80, 'email_enabled': False, 'slack_enabled': True}
                ],
            },
        )


async def enable_stored_slack_alert(alert_service, org_id, team_id=None):
    """Turn on the Slack alert that ``alert_http`` seeds, as an earlier save did."""
    from sqlalchemy import select

    from storage.org_budget_threshold import OrgBudgetThreshold

    session = alert_service.db_session
    settings = await stored_settings(alert_service, org_id)
    threshold = (
        await session.execute(
            select(OrgBudgetThreshold).where(OrgBudgetThreshold.org_id == org_id)
        )
    ).scalar_one()
    settings.slack_channel = '#alerts'
    settings.slack_team_id = team_id
    threshold.slack_enabled = True


async def stored_settings(alert_service, org_id) -> OrgBudgetSettings:
    from sqlalchemy import select

    return (
        await alert_service.db_session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize('already_enabled', [False, True])
async def test_api_save_stores_resolved_slack_workspace(
    alert_http, alert_service, monkeypatch, link_slack, already_enabled
):
    """Saving pins the workspace, also for orgs that enabled Slack before."""
    enable_slack(monkeypatch)
    client, org_id = alert_http
    install_slack_teams(alert_service, 'T_OTHER', 'T_TARGET')
    link = await link_slack(org_id, 'T_TARGET')
    if already_enabled:
        await enable_stored_slack_alert(alert_service, org_id)
    await alert_service.db_session.commit()

    response = await save_slack_alert(client, org_id, [link], '#alerts')

    assert response.status_code == 200, response.text
    assert (await stored_settings(alert_service, org_id)).slack_team_id == 'T_TARGET'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('linked_team_ids', 'expected_team_id'),
    [
        # The org moved workspaces: changing the channel switches to the new one.
        (['T_NEW'], 'T_NEW'),
        # Members' links are ambiguous: keep the stored workspace.
        (['T_NEW', 'T_SIDE'], 'T_OLD'),
    ],
)
async def test_api_channel_change_resolves_slack_workspace_again(
    alert_http,
    alert_service,
    monkeypatch,
    link_slack,
    linked_team_ids,
    expected_team_id,
):
    enable_slack(monkeypatch)
    client, org_id = alert_http
    install_slack_teams(alert_service, 'T_OLD', 'T_NEW', 'T_SIDE')
    links = [await link_slack(org_id, team_id) for team_id in linked_team_ids]
    await enable_stored_slack_alert(alert_service, org_id, team_id='T_OLD')
    await alert_service.db_session.commit()

    response = await save_slack_alert(client, org_id, links, '#new-alerts')

    assert response.status_code == 200, response.text
    assert (
        await stored_settings(alert_service, org_id)
    ).slack_team_id == expected_team_id


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
