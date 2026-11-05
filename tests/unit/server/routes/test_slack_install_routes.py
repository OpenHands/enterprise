"""Slack install flow: Keycloak when OAuth v2 login is off, the v2 session when on."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, quote, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.shared import server_config
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.oauth_v2_refresh import (
    create_oauth_v2_cookie_payload,
    sign_oauth_v2_cookie,
)
from server.routes.integration import slack
from storage.slack_team import SlackTeam
from storage.slack_user import SlackUser

SLACK_USER_ID = 'U123'
TEAM_ID = 'T123'
BOT_TOKEN = 'xoxb-bot-token'


@pytest.fixture
def jwt_svc() -> JwtService:
    return JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('slack-secret'), active=True)]
    )


@pytest.fixture(autouse=True)
def saas_user_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )


@pytest.fixture
def client(jwt_svc: JwtService) -> TestClient:
    app = FastAPI()
    app.include_router(slack.slack_router)
    app.dependency_overrides[slack.jwt_service_dependency.dependency] = lambda: jwt_svc
    return TestClient(app, base_url='https://app.example.com', follow_redirects=False)


@pytest.fixture
def slack_api() -> Iterator[MagicMock]:
    """The Slack Web API: the one service this flow talks to over the network."""
    api = MagicMock()
    api.oauth_v2_access = AsyncMock(
        return_value={
            'access_token': BOT_TOKEN,
            'team': {'id': TEAM_ID},
            'authed_user': {'id': SLACK_USER_ID},
        }
    )
    api.users_info = AsyncMock(
        return_value=MagicMock(data={'user': {'profile': {'display_name': 'Slackie'}}})
    )
    with patch.object(slack, 'AsyncWebClient', return_value=api):
        yield api


@pytest.fixture
def slack_db(oauth_v2_stores, async_session_maker) -> Iterator[None]:
    with (
        patch.object(slack, 'a_session_maker', async_session_maker),
        patch('storage.slack_team_store.a_session_maker', async_session_maker),
    ):
        yield


@pytest.fixture
def receive_message() -> Iterator[AsyncMock]:
    with patch.object(slack.slack_manager, 'receive_message', AsyncMock()) as mock:
        yield mock


@pytest.fixture
def keycloak_stores() -> Iterator[dict[str, AsyncMock]]:
    with (
        patch.object(
            slack.token_manager, 'store_offline_token', AsyncMock()
        ) as offline,
        patch.object(slack.token_manager, 'store_idp_tokens', AsyncMock()) as idp,
    ):
        yield {'offline': offline, 'idp': idp}


def _install_callback(client: TestClient):
    return client.get('/slack/install-callback', params={'code': 'slack-code'})


def _state_from(location: str) -> str:
    return parse_qs(urlparse(location).query)['state'][0]


def _slack_state(jwt_svc: JwtService, **overrides) -> str:
    payload = {
        'slack_user_id': SLACK_USER_ID,
        'bot_access_token': BOT_TOKEN,
        'team_id': TEAM_ID,
        **overrides,
    }
    return jwt_svc.create_jws_token(payload, audience=JwtAudience.SLACK_OAUTH_STATE)


def _session_cookie(user_id: str) -> str:
    return sign_oauth_v2_cookie(
        create_oauth_v2_cookie_payload(user_id, None, True, None), 3600
    )


class TestInstallCallback:
    def test_oauth_v2_on_redirects_to_link_callback_not_keycloak(
        self, client, jwt_svc, slack_api, oauth_v2_login_on
    ):
        response = _install_callback(client)

        assert response.status_code == 307
        location = response.headers['location']
        assert 'openid-connect' not in location
        assert urlparse(location).path == '/slack/link-callback'
        payload = jwt_svc.verify_jws_token(
            _state_from(location), audience=JwtAudience.SLACK_OAUTH_STATE
        )
        assert payload['slack_user_id'] == SLACK_USER_ID
        assert payload['bot_access_token'] == BOT_TOKEN
        assert payload['team_id'] == TEAM_ID

    def test_oauth_v2_off_redirects_to_keycloak(
        self, client, slack_api, oauth_v2_login_off
    ):
        response = _install_callback(client)

        location = response.headers['location']
        assert 'protocol/openid-connect/auth' in location
        assert quote('/slack/keycloak-callback', safe='') in quote(location, safe='')


class TestKeycloakCallback:
    @pytest.mark.asyncio
    async def test_still_links_user_and_stores_keycloak_tokens(
        self,
        client,
        jwt_svc,
        create_user,
        slack_api,
        slack_db,
        receive_message,
        keycloak_stores,
        async_session_maker,
        oauth_v2_login_off,
    ):
        user = create_user()
        keycloak_tokens = AsyncMock(return_value=('kc-access', 'kc-refresh'))
        user_info = AsyncMock(
            return_value=MagicMock(sub=str(user.id), identity_provider='github')
        )
        with (
            patch.object(slack.token_manager, 'get_keycloak_tokens', keycloak_tokens),
            patch.object(slack.token_manager, 'get_user_info', user_info),
        ):
            response = client.get(
                '/slack/keycloak-callback',
                params={'code': 'kc-code', 'state': _slack_state(jwt_svc)},
            )

        assert response.status_code == 200
        async with async_session_maker() as session:
            linked = (await session.execute(select(SlackUser))).scalars().all()
        assert [(u.keycloak_user_id, u.slack_user_id) for u in linked] == [
            (str(user.id), SLACK_USER_ID)
        ]
        keycloak_stores['offline'].assert_awaited_once_with(str(user.id), 'kc-refresh')
        keycloak_stores['idp'].assert_awaited_once_with(
            ProviderType.GITHUB, str(user.id), 'kc-access'
        )
        receive_message.assert_awaited_once()


class TestLinkCallback:
    def test_unavailable_when_oauth_v2_off(self, client, jwt_svc, oauth_v2_login_off):
        response = client.get(
            '/slack/link-callback', params={'state': _slack_state(jwt_svc)}
        )

        assert response.status_code == 404

    def test_without_session_redirects_to_login_and_returns_here(
        self, client, jwt_svc, oauth_v2_stores, oauth_v2_login_on
    ):
        state = _slack_state(jwt_svc)

        response = client.get('/slack/link-callback', params={'state': state})

        assert response.status_code == 307
        location = urlparse(response.headers['location'])
        assert location.path == '/login'
        return_to = parse_qs(location.query)['returnTo'][0]
        assert urlparse(return_to).path == '/slack/link-callback'
        assert _state_from(return_to) == state

    def test_rejects_invalid_state(
        self, client, create_user, oauth_v2_stores, oauth_v2_login_on
    ):
        user = create_user()
        client.cookies.set('openhands_auth', _session_cookie(str(user.id)))

        response = client.get('/slack/link-callback', params={'state': 'garbage'})

        assert response.status_code == 400

    def test_state_for_another_purpose_is_rejected(
        self, client, jwt_svc, create_user, oauth_v2_stores, oauth_v2_login_on
    ):
        user = create_user()
        client.cookies.set('openhands_auth', _session_cookie(str(user.id)))
        state = jwt_svc.create_jws_token(
            {'slack_user_id': SLACK_USER_ID, 'bot_access_token': BOT_TOKEN},
            audience=JwtAudience.SESSION,
        )

        response = client.get('/slack/link-callback', params={'state': state})

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_links_session_user_without_keycloak(
        self,
        client,
        jwt_svc,
        create_user,
        slack_api,
        slack_db,
        receive_message,
        keycloak_stores,
        no_keycloak,
        async_session_maker,
        oauth_v2_login_on,
    ):
        user = create_user()
        client.cookies.set('openhands_auth', _session_cookie(str(user.id)))

        response = client.get(
            '/slack/link-callback', params={'state': _slack_state(jwt_svc)}
        )

        assert response.status_code == 200
        async with async_session_maker() as session:
            slack_users = (await session.execute(select(SlackUser))).scalars().all()
            teams = (await session.execute(select(SlackTeam))).scalars().all()
        assert [
            (u.keycloak_user_id, u.org_id, u.slack_user_id, u.slack_display_name)
            for u in slack_users
        ] == [(str(user.id), user.current_org_id, SLACK_USER_ID, 'Slackie')]
        assert [(t.team_id, t.bot_access_token) for t in teams] == [
            (TEAM_ID, BOT_TOKEN)
        ]
        keycloak_stores['offline'].assert_not_awaited()
        keycloak_stores['idp'].assert_not_awaited()
        receive_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_relinking_replaces_previous_link(
        self,
        client,
        jwt_svc,
        create_user,
        slack_api,
        slack_db,
        receive_message,
        no_keycloak,
        async_session_maker,
        oauth_v2_login_on,
    ):
        first, second = create_user(), create_user()
        for user in (first, second):
            client.cookies.set('openhands_auth', _session_cookie(str(user.id)))
            client.get('/slack/link-callback', params={'state': _slack_state(jwt_svc)})

        async with async_session_maker() as session:
            linked = (
                (await session.execute(select(SlackUser.keycloak_user_id)))
                .scalars()
                .all()
            )
        assert linked == [str(second.id)]
