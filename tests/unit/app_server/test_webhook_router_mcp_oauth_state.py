"""Tests for ``POST /api/v1/webhooks/mcp-oauth-state``.

A sandbox posts the MCP OAuth state FastMCP refreshed for an inline server;
the route stores it on the owner's matching ``oauth2`` server so the next
conversation starts from the current (for rotating providers, only valid)
refresh token. When no personal server matches, the owner's organization MCP
listings at that URL take the state (OHE-3091).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

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


def _client(
    store: _FakeSettingsStore | None = None, credential_org_id: UUID | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    if store is not None:
        app.dependency_overrides[valid_sandbox] = lambda: SandboxRecord(
            id='sandbox-1',
            created_by_user_id='user-1',
            credential_org_id=credential_org_id,
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


def _patch_org_write_back(written: list[str]):
    """The owner's org store: ``(user patch, store patch, store mock, user)``."""
    user = SimpleNamespace(
        id=UUID('11111111-1111-1111-1111-111111111111'),
        current_org_id=UUID('22222222-2222-2222-2222-222222222222'),
    )
    org_store = SimpleNamespace(write_back_oauth_state=AsyncMock(return_value=written))
    return (
        patch(
            'storage.user_store.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch(
            'storage.org_mcp_servers_store.OrgMcpServersStore',
            return_value=org_store,
        ),
        org_store,
        user,
    )


def test_rejects_state_for_a_server_without_an_oauth_credential():
    # Arrange: the URL is known, but the server uses a bearer credential, and
    # the owner's organization has no OAuth listing at that URL either.
    store = _FakeSettingsStore(_settings({'strategy': 'bearer', 'value': 'token'}))
    user_patch, store_patch, org_store, _user = _patch_org_write_back([])

    # Act
    with user_patch, store_patch:
        response = _post(_client(store), store, GITLAB_URL)

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert store.stored == []
    org_store.write_back_oauth_state.assert_awaited_once()


def test_falls_back_to_the_owners_organization_listings():
    """OHE-3091: an org MCP listing launches inline like a personal server, so
    its refreshed tokens come back here too and go to the listing (org-managed)
    or the owner's own state (user-managed) by URL."""
    # Arrange: no personal server at the URL.
    store = _FakeSettingsStore(_settings({'strategy': 'bearer', 'value': 'token'}))
    user_patch, store_patch, org_store, user = _patch_org_write_back(['jira'])

    # Act
    with user_patch, store_patch as store_cls:
        response = _post(_client(store), store, 'https://jira.example/mcp/')

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert store.stored == []
    store_cls.assert_called_once_with(user.current_org_id)
    user_id, server_url, state = org_store.write_back_oauth_state.call_args.args
    assert user_id == user.id
    assert server_url == 'https://jira.example/mcp'
    assert state.tokens.access_token.get_secret_value() == 'new-access-token'
    assert state.tokens.refresh_token.get_secret_value() == 'new-refresh-token'
    assert state.token_expires_at == 123.0


def test_uses_the_organization_the_sandbox_launched_under():
    # Arrange: the owner has since switched to another current organization.
    launch_org = UUID('33333333-3333-3333-3333-333333333333')
    store = _FakeSettingsStore(_settings({'strategy': 'bearer', 'value': 'token'}))
    user_patch, store_patch, _org_store, user = _patch_org_write_back(['jira'])
    assert user.current_org_id != launch_org

    # Act
    with user_patch, store_patch as store_cls:
        response = _post(
            _client(store, credential_org_id=launch_org),
            store,
            'https://jira.example/mcp',
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    store_cls.assert_called_once_with(launch_org)


def test_personal_server_wins_over_the_organization_listings():
    store = _FakeSettingsStore(_settings({'strategy': 'oauth2'}))
    user_patch, store_patch, org_store, _user = _patch_org_write_back(['gitlab'])

    with user_patch, store_patch:
        response = _post(_client(store), store, GITLAB_URL)

    assert response.status_code == status.HTTP_200_OK
    assert len(store.stored) == 1
    org_store.write_back_oauth_state.assert_not_called()


def test_requires_a_session_api_key():
    # Act: no ``X-Session-API-Key`` header, no dependency override.
    response = _client().post(
        '/api/v1/webhooks/mcp-oauth-state',
        json={'server_url': GITLAB_URL, 'oauth_state': REFRESHED_STATE},
    )

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
