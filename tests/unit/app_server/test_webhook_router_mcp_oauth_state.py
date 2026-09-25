"""Tests for ``POST /api/v1/webhooks/mcp-oauth-state``.

A sandbox posts the MCP OAuth state FastMCP refreshed for an inline server;
the route stores it on the owner's matching ``oauth2`` server so the next
conversation starts from the current (for rotating providers, only valid)
refresh token.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.event_callback.webhook_router import (
    router,
    valid_sandbox,
)
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.settings.settings_models import Settings
from openhands.sdk.mcp.config import MCPServer

GITLAB_URL = 'https://gitlab.example/api/v4/mcp'
JIRA_TOKEN = 'Bearer jira-secret-token'
REFRESHED_STATE = {
    'tokens': {
        'access_token': 'new-access-token',
        'refresh_token': 'new-refresh-token',
    },
    'client_info': {'client_id': 'client-id'},
    'token_expires_at': 123.0,
}


class _FakeSettingsStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stored: list[Settings] = []

    async def load(self) -> Settings:
        return self.settings

    async def store(self, item: Settings) -> None:
        self.stored.append(item)


def _settings(gitlab_auth: dict) -> Settings:
    settings = Settings()
    settings.agent_settings.mcp_config = {
        'gitlab': MCPServer.model_validate({'url': GITLAB_URL, 'auth': gitlab_auth}),
        'jira': MCPServer(
            url='https://jira.example/mcp',
            headers={'Authorization': SecretStr(JIRA_TOKEN)},
        ),
    }
    return settings


def _client(store: _FakeSettingsStore | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    if store is not None:
        app.dependency_overrides[valid_sandbox] = lambda: SandboxRecord(
            id='sandbox-1', created_by_user_id='user-1'
        )
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, store: _FakeSettingsStore, server_url: str):
    with patch(
        'openhands.app_server.shared.SettingsStoreImpl',
        SimpleNamespace(get_instance=AsyncMock(return_value=store)),
    ):
        return client.post(
            '/api/v1/webhooks/mcp-oauth-state',
            json={'server_url': server_url, 'oauth_state': REFRESHED_STATE},
        )


def test_persists_refreshed_state_on_the_owners_oauth_server():
    # Arrange
    store = _FakeSettingsStore(
        _settings(
            {
                'strategy': 'oauth2',
                'state': {
                    'tokens': {
                        'access_token': 'old-access-token',
                        'refresh_token': 'old-refresh-token',
                    },
                    'token_expires_at': 1.0,
                },
            }
        )
    )

    # Act: the sandbox reports the URL with a trailing slash, as FastMCP keys it.
    response = _post(_client(store), store, f'{GITLAB_URL}/')

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert len(store.stored) == 1
    saved = store.stored[0]
    # ``SaasSettingsStore.store`` only persists ``mcp_config`` when flagged.
    assert saved._mcp_config_updated is True
    state = saved.agent_settings.mcp_config['gitlab'].auth.state
    assert state.tokens.access_token.get_secret_value() == 'new-access-token'
    assert state.tokens.refresh_token.get_secret_value() == 'new-refresh-token'
    assert state.client_info.model_dump()['client_id'] == 'client-id'
    assert state.token_expires_at == 123.0
    # The sibling still sends the same credential (the catalog replay
    # normalizes a bare ``Authorization`` header into a bearer credential).
    jira = saved.agent_settings.mcp_config['jira']
    assert jira.auth is not None
    assert jira.auth.to_http_headers() == {'Authorization': JIRA_TOKEN}


def test_rejects_state_for_a_server_without_an_oauth_credential():
    # Arrange: the URL is known, but the server uses a bearer credential.
    store = _FakeSettingsStore(_settings({'strategy': 'bearer', 'value': 'token'}))

    # Act
    response = _post(_client(store), store, GITLAB_URL)

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert store.stored == []


def test_requires_a_session_api_key():
    # Act: no ``X-Session-API-Key`` header, no dependency override.
    response = _client().post(
        '/api/v1/webhooks/mcp-oauth-state',
        json={'server_url': GITLAB_URL, 'oauth_state': REFRESHED_STATE},
    )

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
