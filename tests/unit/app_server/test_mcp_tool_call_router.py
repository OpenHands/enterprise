"""Unit tests for ``POST /api/v1/mcp/servers/{server_name}/call-tool``.

The SDK probe and DNS resolution are mocked: these tests cover the route's own
behaviour - calling a tool on a server the caller has connected with the
credentials stored in their settings, keeping the OAuth tokens a call
refreshed, and failing a call that would need the user's consent.
"""

import asyncio
from ipaddress import ip_address
from unittest.mock import patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from openhands.agent_server.mcp_router import (
    MCPOAuthStateResponse,
    MCPTestFailure,
    MCPTestSuccess,
    MCPToolCallResult,
)
from openhands.app_server.mcp.mcp_tool_call_router import router
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.user_auth import get_user_settings_store
from openhands.app_server.utils.dependencies import check_session_api_key
from openhands.sdk.mcp.config import MCPServer

SERVER_NAME = 'atlassian-rovo'
CALL_TOOL_URL = f'/api/v1/mcp/servers/{SERVER_NAME}/call-tool'
STORED_ACCESS_TOKEN = 'stored-access-token'
STORED_STATE = {
    'tokens': {
        'access_token': STORED_ACCESS_TOKEN,
        'refresh_token': 'stored-refresh-token',
    },
    'client_info': {'client_id': 'client-id'},
    'token_expires_at': 1.0,
}
REFRESHED_STATE = {
    'tokens': {
        'access_token': 'new-access-token',
        'refresh_token': 'new-refresh-token',
    },
    'client_info': {'client_id': 'client-id'},
    'token_expires_at': 123.0,
}
TOOL_CALL = {
    'name': 'searchJiraIssuesUsingJql',
    'arguments': {'jql': 'labels = "create-pr"'},
}
TOOL_RESULT = MCPToolCallResult(is_error=False, text='{"issues": []}')


class _FakeSettingsStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stored: list[Settings] = []

    async def load(self) -> Settings:
        return self.settings

    async def store(self, item: Settings) -> None:
        self.stored.append(item)


def _store(enabled: bool = True) -> _FakeSettingsStore:
    settings = Settings()
    settings.agent_settings.mcp_config = {
        SERVER_NAME: MCPServer.model_validate(
            {
                'url': 'https://mcp.atlassian.example/v1/mcp',
                'enabled': enabled,
                'auth': {'strategy': 'oauth2', 'state': STORED_STATE},
            }
        )
    }
    return _FakeSettingsStore(settings)


def _client(store: _FakeSettingsStore) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[check_session_api_key] = lambda: None
    app.dependency_overrides[get_user_settings_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def probe():
    with patch(
        'openhands.app_server.mcp.mcp_tool_call_router._probe_mcp_server'
    ) as mock:
        mock.return_value = MCPTestSuccess(
            tools=[TOOL_CALL['name']],
            tool_result=TOOL_RESULT,
            oauth_state=MCPOAuthStateResponse(**STORED_STATE),
        )
        yield mock


@pytest.fixture(autouse=True)
def resolve():
    """Let the shared SSRF guard treat the MCP host as public."""
    with patch(
        'openhands.app_server.mcp.mcp_test_router._resolve_probe_addresses'
    ) as mock:
        mock.return_value = [ip_address('203.0.113.10')]
        yield mock


def test_calls_the_tool_on_a_connected_server_with_its_stored_credentials(probe):
    # Arrange
    client = _client(_store())

    # Act
    response = client.post(CALL_TOOL_URL, json=TOOL_CALL)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {'is_error': False, 'text': '{"issues": []}'}
    request = probe.call_args.args[0]
    assert request.tool_call.name == TOOL_CALL['name']
    assert request.tool_call.arguments == TOOL_CALL['arguments']
    seeded = request.resolved_server.initial_oauth_state()
    assert seeded.tokens.access_token.get_secret_value() == STORED_ACCESS_TOKEN


@pytest.mark.parametrize(
    'store, server_name',
    [
        pytest.param(_store(), 'never-connected', id='unknown'),
        pytest.param(_store(enabled=False), SERVER_NAME, id='switched off'),
    ],
)
def test_reports_a_server_the_caller_has_not_connected_as_not_found(
    probe, store, server_name
):
    # Act
    response = _client(store).post(
        f'/api/v1/mcp/servers/{server_name}/call-tool', json=TOOL_CALL
    )

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    probe.assert_not_called()


def test_stores_the_tokens_a_call_refreshed(probe):
    # Arrange
    store = _store()
    probe.return_value = MCPTestSuccess(
        tools=[TOOL_CALL['name']],
        tool_result=TOOL_RESULT,
        oauth_state=MCPOAuthStateResponse(**REFRESHED_STATE),
    )

    # Act
    response = _client(store).post(CALL_TOOL_URL, json=TOOL_CALL)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert len(store.stored) == 1
    saved = store.stored[0]
    # ``SaasSettingsStore.store`` only persists ``mcp_config`` when flagged.
    assert saved._mcp_config_updated is True
    state = saved.agent_settings.mcp_config[SERVER_NAME].auth.state
    assert state.tokens.access_token.get_secret_value() == 'new-access-token'
    assert state.tokens.refresh_token.get_secret_value() == 'new-refresh-token'
    assert state.token_expires_at == 123.0


def test_leaves_settings_untouched_when_the_tokens_did_not_change(probe):
    # Arrange: the probe hands back the state it was seeded with.
    store = _store()

    # Act
    response = _client(store).post(CALL_TOOL_URL, json=TOOL_CALL)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert store.stored == []


def test_reports_a_server_that_cannot_be_called_without_leaking_its_tokens(probe):
    # Arrange: the upstream error echoes the credential the call was made with.
    probe.return_value = MCPTestFailure(
        error=f'401 Unauthorized for token {STORED_ACCESS_TOKEN}',
        error_kind='connection',
    )

    # Act
    response = _client(_store()).post(CALL_TOOL_URL, json=TOOL_CALL)

    # Assert
    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert STORED_ACCESS_TOKEN not in response.text


def test_fails_a_call_that_needs_the_users_consent_instead_of_waiting_for_it(probe):
    # Arrange: stand in for the SDK probe, which reports whatever the OAuth
    # client raises once the provider asks for the user's consent again.
    def needs_consent(request, cipher, mcp_oauth_factory=None):
        server = request.resolved_server
        oauth = mcp_oauth_factory(SERVER_NAME, server, server.oauth_auth, None)
        try:
            asyncio.run(
                oauth.redirect_handler('https://auth.example/authorize?state=s')
            )
        except RuntimeError as exc:
            return MCPTestFailure(error=str(exc), error_kind='unknown')
        return MCPTestSuccess(tools=[], tool_result=TOOL_RESULT)

    probe.side_effect = needs_consent

    # Act
    response = _client(_store()).post(CALL_TOOL_URL, json=TOOL_CALL)

    # Assert
    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert 'authorized again' in response.json()['detail']
