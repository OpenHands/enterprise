"""Local Slack linking continues with the proven OpenHands session."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import SecretStr
from sqlalchemy import select

from integrations.models import Message, SourceType
from integrations.slack.slack_manager import SlackManager
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import mode
from server.auth.contracts import Principal
from server.auth.saas_user_auth import SaasUserAuth
from server.routes.integration import slack
from storage.slack_user import SlackUser
from storage.user import User
from tests.unit.server.auth.test_shared_authentication import (
    auth_database as auth_database,
)

USER_ID = UUID('11111111-1111-4111-8111-111111111111')


@pytest.fixture
def local_link(monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    auth = SaasUserAuth(
        user_id=str(USER_ID),
        principal=Principal(
            USER_ID, 'password', datetime.now(UTC), session_id='safe-session-digest'
        ),
    )
    monkeypatch.setattr(slack, 'get_user_auth', AsyncMock(return_value=auth))
    monkeypatch.setattr(
        slack,
        'token_manager',
        MagicMock(
            get_keycloak_tokens=AsyncMock(
                side_effect=AssertionError('Local Slack called Keycloak')
            )
        ),
    )
    service = JwtService(
        keys=[
            EncryptionKey(
                kid='test', key=SecretStr('slack-local-test-secret'), active=True
            )
        ]
    )
    return auth, service


def request(cookies=None):
    req = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': '/slack/install-callback',
            'scheme': 'https',
            'server': ('app.example.com', 443),
            'headers': [],
            'query_string': b'',
        }
    )
    req._cookies = cookies or {}
    return req


async def begin(service):
    response = await slack.install(request(), jwt_service=service)
    state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    payload = service.verify_jws_token(state)
    return state, payload['nonce'], response


async def test_local_install_state_is_bound_to_principal_and_session(local_link):
    auth, service = local_link
    state, nonce, response = await begin(service)
    payload = service.verify_jws_token(state)
    assert payload['user_id'] == auth.user_id
    assert payload['session'] == 'password:safe-session-digest'
    assert 'bot_access_token' not in payload
    assert nonce in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'Secure' in response.headers['set-cookie']


async def test_local_callback_completes_without_keycloak_or_token_redirect(
    local_link, monkeypatch
):
    _, service = local_link
    state, nonce, _ = await begin(service)
    client = MagicMock(
        oauth_v2_access=AsyncMock(
            return_value={
                'access_token': 'slack-bot-secret',
                'team': {'id': 'T1'},
                'authed_user': {'id': 'U1'},
            }
        )
    )
    monkeypatch.setattr(slack, 'AsyncWebClient', lambda **kwargs: client)
    user = MagicMock(id=USER_ID, is_disabled=False)
    monkeypatch.setattr(slack.UserStore, 'get_user_by_id', AsyncMock(return_value=user))
    complete = AsyncMock(return_value=HTMLResponse('connected'))
    monkeypatch.setattr(slack, '_complete_slack_link', complete)
    response = await slack.install_callback(
        request({'oh_slack_oauth': nonce}),
        BackgroundTasks(),
        code='slack-code',
        state=state,
        jwt_service=service,
    )
    assert response.status_code == 200
    assert 'location' not in response.headers
    assert b'slack-bot-secret' not in response.body
    assert complete.call_args.args[1]['slack_user_id'] == 'U1'
    assert complete.call_args.args[1]['bot_access_token'] == 'slack-bot-secret'
    slack.token_manager.get_keycloak_tokens.assert_not_awaited()


@pytest.mark.parametrize('changed', ['nonce', 'session', 'account'])
async def test_local_callback_rejects_changed_connection_binding(
    local_link, monkeypatch, changed
):
    auth, service = local_link
    state, nonce, _ = await begin(service)
    if changed == 'nonce':
        nonce = 'another-nonce'
    elif changed == 'session':
        auth.principal = Principal(
            USER_ID, 'password', datetime.now(UTC), session_id='replacement-session'
        )
    else:
        auth.user_id = str(UUID('22222222-2222-4222-8222-222222222222'))
    client = MagicMock()
    monkeypatch.setattr(slack, 'AsyncWebClient', client)
    with pytest.raises(HTTPException) as exc:
        await slack.install_callback(
            request({'oh_slack_oauth': nonce}),
            BackgroundTasks(),
            code='slack-code',
            state=state,
            jwt_service=service,
        )
    assert exc.value.status_code == 400
    client.assert_not_called()


async def test_keycloak_callback_is_inactive_before_any_exchange(local_link):
    _, service = local_link
    with pytest.raises(HTTPException) as exc:
        await slack.keycloak_callback(
            request(), BackgroundTasks(), code='code', jwt_service=service
        )
    assert exc.value.status_code == 404
    slack.token_manager.get_keycloak_tokens.assert_not_awaited()


@pytest.mark.parametrize('disabled', [False, True])
async def test_legacy_slack_callback_hydrates_proven_subject_before_linking(
    local_link, monkeypatch, disabled
):
    from server.auth.keycloak.token_manager import KeycloakUserInfo

    _, service = local_link
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    info = KeycloakUserInfo(
        sub=str(USER_ID), email='existing@example.com', email_verified=True
    )
    tokens = MagicMock(
        get_keycloak_tokens=AsyncMock(return_value=('access', 'refresh')),
        get_user_info=AsyncMock(return_value=info),
        store_offline_token=AsyncMock(),
        store_idp_tokens=AsyncMock(),
    )
    monkeypatch.setattr(slack, 'token_manager', tokens)
    hydrate = AsyncMock(return_value=MagicMock(id=USER_ID, is_disabled=disabled))
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
        hydrate,
    )
    complete = AsyncMock(return_value=HTMLResponse('connected'))
    monkeypatch.setattr(slack, '_complete_slack_link', complete)
    response = await slack.keycloak_callback(
        request(),
        BackgroundTasks(),
        code='code',
        state=service.create_jws_token({'slack_user_id': 'U1', 'team_id': 'T1'}),
        jwt_service=service,
    )
    hydrate.assert_awaited_once_with(USER_ID, info.model_dump(exclude_none=True))
    assert response.status_code == (400 if disabled else 200)
    assert complete.await_count == (0 if disabled else 1)
    if disabled:
        tokens.store_offline_token.assert_not_awaited()


async def test_message_generated_login_link_enters_session_bound_install(
    local_link, monkeypatch
):
    _, service = local_link
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: service)
    monkeypatch.setattr('server.auth.slack_link.HOST_URL', 'https://app.example.com')
    message = Message(
        source=SourceType.SLACK,
        message={'slack_user_id': 'U1', 'team_id': 'T1', 'user_msg': 'hello'},
    )
    link = SlackManager(MagicMock())._generate_login_link_with_state(message)
    assert urlsplit(link).path == '/slack/install'
    assert slack._generate_login_link() == 'https://app.example.com/slack/install'
    original_state = parse_qs(urlsplit(link).query)['state'][0]
    response = await slack.install(request(), original_state, service)
    state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    claims = service.verify_jws_token(state)
    assert claims['message']['slack_user_id'] == 'U1'
    assert claims['session'] == 'password:safe-session-digest'
    assert claims['nonce'] in response.headers['set-cookie']


@pytest.mark.parametrize('existing_owner', ['self', 'other'])
async def test_local_slack_actor_cannot_transfer_to_another_account(
    auth_database,  # noqa: F811
    monkeypatch,
    existing_owner,  # noqa: F811
):
    factory, user, _ = auth_database
    monkeypatch.setattr(slack, 'a_session_maker', factory)
    monkeypatch.setattr(slack.slack_team_store, 'create_team', AsyncMock())
    monkeypatch.setattr(
        slack,
        'AsyncWebClient',
        lambda **kwargs: MagicMock(
            users_info=AsyncMock(
                return_value={'user': {'profile': {'display_name': 'Test actor'}}}
            )
        ),
    )
    owner = str(user.id) if existing_owner == 'self' else str(USER_ID)
    async with factory() as session, session.begin():
        session.add(
            SlackUser(
                keycloak_user_id=owner,
                slack_user_id='U1',
                slack_display_name='Existing actor',
            )
        )
    task = slack._complete_slack_link(
        user,
        {'slack_user_id': 'U1', 'team_id': 'T1', 'bot_access_token': 'bot-secret'},
        BackgroundTasks(),
    )
    if existing_owner == 'other':
        with pytest.raises(HTTPException) as exc:
            await task
        assert exc.value.status_code == 409
    else:
        assert (await task).status_code == 200
    async with factory() as session:
        rows = (await session.scalars(select(SlackUser))).all()
        assert len(rows) == 1
        assert rows[0].keycloak_user_id == owner


async def test_local_slack_link_rechecks_account_disabled_after_oauth(
    auth_database,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    factory, user, _ = auth_database
    monkeypatch.setattr(slack, 'a_session_maker', factory)
    monkeypatch.setattr(slack.slack_team_store, 'create_team', AsyncMock())

    async def slack_info(**kwargs):
        async with factory() as session, session.begin():
            (await session.get(User, user.id)).is_disabled = True
        return {'user': {'profile': {'display_name': 'Test actor'}}}

    monkeypatch.setattr(
        slack,
        'AsyncWebClient',
        lambda **kwargs: MagicMock(users_info=slack_info),
    )
    with pytest.raises(HTTPException) as exc:
        await slack._complete_slack_link(
            user,
            {'slack_user_id': 'U1', 'team_id': 'T1', 'bot_access_token': 'bot-secret'},
            BackgroundTasks(),
        )
    assert exc.value.status_code == 401
    async with factory() as session:
        assert not (await session.scalars(select(SlackUser))).all()
