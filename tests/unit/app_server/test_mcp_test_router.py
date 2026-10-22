"""Unit tests for the ``POST /api/v1/mcp/test`` route.

The SDK probe and DNS resolution are mocked: these tests cover the route's own
behaviour — restoring redacted secrets from stored settings, rejecting configs
and targets that must not be probed from the app server, and scrubbing secrets
from the response.
"""

from ipaddress import ip_address
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.mcp_router import (
    MCPTestFailure,
    MCPTestRequest,
    MCPTestSuccess,
    MCPToolCallResult,
)
from openhands.app_server.integrations.provider import ProviderToken
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.mcp.mcp_test_router import router, user_context_dependency
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.user_auth import get_user_settings
from openhands.app_server.utils.dependencies import check_session_api_key
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.secret import StaticSecret

MCP_URL = 'https://mcp-jira.example.com/mcp'
STORED_TOKEN = 'Bearer stored-secret-token'
REDACTED = '**********'


def _settings_with_stored_server() -> Settings:
    settings = Settings()
    settings.agent_settings.mcp_config = {
        'jira': MCPServer(
            url=MCP_URL, headers={'Authorization': SecretStr(STORED_TOKEN)}
        )
    }
    return settings


def _user_context(
    secrets: dict[str, str] | None = None,
    provider_tokens: dict[ProviderType, str] | None = None,
) -> MagicMock:
    user_context = MagicMock()
    user_context.get_secrets = AsyncMock(
        return_value={
            name: StaticSecret(value=SecretStr(value))
            for name, value in (secrets or {}).items()
        }
    )
    tokens = provider_tokens or {}
    user_context.get_provider_tokens = AsyncMock(
        return_value={
            provider: ProviderToken(token=SecretStr('stale-' + token))
            for provider, token in tokens.items()
        }
    )
    user_context.get_latest_token = AsyncMock(side_effect=tokens.get)
    return user_context


def _client(
    settings: Settings | None, user_context: MagicMock | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[check_session_api_key] = lambda: None
    app.dependency_overrides[get_user_settings] = lambda: settings
    context = user_context or _user_context()
    app.dependency_overrides[user_context_dependency.dependency] = lambda: context
    return TestClient(app, raise_server_exceptions=False)


def _probed_request(probe) -> MCPTestRequest:
    request = probe.call_args.args[0]
    assert isinstance(request, MCPTestRequest)
    return request


def _probed_headers(probe) -> dict[str, str]:
    # Settings normalisation may carry a bearer token either as a raw header
    # or as an ``auth`` credential; return the effective headers.
    probed = _probed_request(probe).resolved_server
    headers = {
        key: value.get_secret_value() for key, value in (probed.headers or {}).items()
    }
    if probed.auth is not None:
        headers.update(probed.auth.to_http_headers() or {})
    return headers


@pytest.fixture
def probe():
    with patch('openhands.app_server.mcp.mcp_test_router._probe_mcp_server') as mock:
        mock.return_value = MCPTestSuccess(tools=['echo'])
        yield mock


@pytest.fixture(autouse=True)
def resolve():
    """Resolve every host to a public address unless a test says otherwise."""
    with patch(
        'openhands.app_server.mcp.mcp_test_router._resolve_probe_addresses'
    ) as mock:
        mock.return_value = [ip_address('203.0.113.10')]
        yield mock


def test_probes_remote_server_and_returns_tools(probe):
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test', json={'server': {'url': MCP_URL, 'transport': 'sse'}}
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'tools': ['echo']}
    probed = _probed_request(probe).resolved_server
    assert probed.url == MCP_URL
    assert probed.transport == 'sse'


def test_restores_redacted_secret_from_stored_server(probe):
    client = _client(_settings_with_stored_server())

    client.post(
        '/api/v1/mcp/test',
        json={
            'name': 'jira',
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': f'Bearer {REDACTED}'},
            },
        },
    )

    assert _probed_headers(probe)['Authorization'] == STORED_TOKEN


def test_drops_redaction_marker_without_stored_server(probe):
    client = _client(settings=None)

    client.post(
        '/api/v1/mcp/test',
        json={'server': {'url': MCP_URL, 'headers': {'Authorization': REDACTED}}},
    )

    headers = _probed_request(probe).resolved_server.headers or {}
    assert 'Authorization' not in headers


def test_rejects_stdio_servers(probe):
    client = _client(settings=None)

    response = client.post('/api/v1/mcp/test', json={'server': {'command': 'npx'}})

    assert response.status_code == 422
    assert 'stdio' in response.json()['detail']
    probe.assert_not_called()


def test_rejects_non_http_urls(probe):
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test', json={'server': {'url': 'ftp://mcp.example.com/mcp'}}
    )

    assert response.status_code == 422
    assert 'http' in response.json()['detail']
    probe.assert_not_called()


@pytest.mark.parametrize(
    'address',
    ['127.0.0.1', '169.254.169.254', '::1', '::ffff:169.254.169.254', '0.0.0.0'],
)
def test_rejects_targets_resolving_to_local_addresses(probe, resolve, address):
    resolve.return_value = [ip_address(address)]
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test', json={'server': {'url': 'https://mcp.example.com/mcp'}}
    )

    assert response.status_code == 422
    assert 'cannot be probed' in response.json()['detail']
    probe.assert_not_called()


def test_allows_private_network_targets(probe, resolve):
    # Self-hosted deployments point at MCP servers on their internal network.
    resolve.return_value = [ip_address('10.20.30.40')]
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test', json={'server': {'url': 'https://mcp.corp.internal/mcp'}}
    )

    assert response.status_code == 200
    assert response.json()['ok'] is True
    resolve.assert_called_once_with('mcp.corp.internal', None)


def test_reports_oauth_servers_as_untestable(probe):
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test',
        json={'server': {'url': MCP_URL, 'auth': {'strategy': 'oauth2'}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is False
    assert body['error_kind'] == 'unknown'
    probe.assert_not_called()


def test_rejects_invalid_request(probe):
    client = _client(settings=None)

    response = client.post('/api/v1/mcp/test', json={'server': {'transport': 'sse'}})

    assert response.status_code == 422
    probe.assert_not_called()


def test_scrubs_secrets_from_failure_text(probe):
    probe.return_value = MCPTestFailure(
        error=f"401 Unauthorized for '{MCP_URL}' with {STORED_TOKEN}",
        error_kind='connection',
    )
    client = _client(_settings_with_stored_server())

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'name': 'jira',
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': f'Bearer {REDACTED}'},
            },
        },
    )

    body = response.json()
    assert body['ok'] is False
    assert STORED_TOKEN not in body['error']
    assert REDACTED in body['error']


def test_scrubs_url_encoded_secret_from_failure_text(probe):
    probe.return_value = MCPTestFailure(
        error=f"redirect to '{MCP_URL}?token=Bearer%20stored-secret-token' failed",
        error_kind='connection',
    )
    client = _client(_settings_with_stored_server())

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'name': 'jira',
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': f'Bearer {REDACTED}'},
            },
        },
    )

    body = response.json()
    assert 'stored-secret-token' not in body['error']
    assert REDACTED in body['error']


def test_scrubs_secrets_from_tool_result_text(probe):
    probe.return_value = MCPTestSuccess(
        tools=['whoami'],
        tool_result=MCPToolCallResult(is_error=True, text='invalid key: typed-key'),
    )
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'server': {
                'url': MCP_URL,
                'auth': {
                    'strategy': 'api_key',
                    'value': 'typed-key',
                    'header_name': 'X-Key',
                },
            }
        },
    )

    body = response.json()
    assert body['ok'] is True
    assert body['tool_result']['text'] == f'invalid key: {REDACTED}'


def test_expands_secret_reference_from_user_secrets(probe):
    client = _client(
        settings=None, user_context=_user_context({'JIRA_TOKEN': 'jira-secret'})
    )

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': 'Bearer ${JIRA_TOKEN}'},
            }
        },
    )

    assert response.json() == {'ok': True, 'tools': ['echo']}
    assert _probed_headers(probe)['Authorization'] == 'Bearer jira-secret'


def test_expands_reference_restored_from_stored_server(probe):
    settings = Settings()
    settings.agent_settings.mcp_config = {
        'jira': MCPServer(
            url=MCP_URL, headers={'Authorization': SecretStr('Bearer ${JIRA_TOKEN}')}
        )
    }
    client = _client(settings, _user_context({'JIRA_TOKEN': 'jira-secret'}))

    client.post(
        '/api/v1/mcp/test',
        json={
            'name': 'jira',
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': f'Bearer {REDACTED}'},
            },
        },
    )

    assert _probed_headers(probe)['Authorization'] == 'Bearer jira-secret'


def test_git_provider_token_reference_uses_latest_token(probe):
    user_context = _user_context(
        {'GITHUB_TOKEN': 'custom-value'}, {ProviderType.GITHUB: 'gh-latest'}
    )
    client = _client(settings=None, user_context=user_context)

    client.post(
        '/api/v1/mcp/test',
        json={
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': 'Bearer ${GITHUB_TOKEN}'},
            }
        },
    )

    # A conversation's provider token secret overrides a custom secret too.
    assert _probed_headers(probe)['Authorization'] == 'Bearer gh-latest'


def test_reports_unset_secret_reference_by_name(probe):
    client = _client(settings=None, user_context=_user_context({'OTHER': 'x'}))

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': 'Bearer ${JIRA_TOKN}'},
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is False
    assert '${JIRA_TOKN}' in body['error']
    probe.assert_not_called()


def test_applies_default_for_unset_reference(probe):
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'server': {
                'url': MCP_URL,
                'headers': {'X-Team': '${JIRA_TEAM:-platform}'},
            }
        },
    )

    assert response.json()['ok'] is True
    assert _probed_headers(probe)['X-Team'] == 'platform'


def test_never_expands_from_app_server_environment(probe, monkeypatch):
    monkeypatch.setenv('DB_PASS', 'app-server-env-value')
    client = _client(settings=None)

    response = client.post(
        '/api/v1/mcp/test',
        json={
            'server': {
                'url': MCP_URL,
                'headers': {'Authorization': 'Bearer ${DB_PASS}'},
            }
        },
    )

    body = response.json()
    assert body['ok'] is False
    assert 'app-server-env-value' not in body['error']
    probe.assert_not_called()


def test_server_without_references_does_not_load_secrets(probe):
    user_context = _user_context({'JIRA_TOKEN': 'jira-secret'})
    client = _client(settings=None, user_context=user_context)

    client.post(
        '/api/v1/mcp/test',
        json={'server': {'url': MCP_URL, 'headers': {'Authorization': 'Bearer x'}}},
    )

    user_context.get_secrets.assert_not_called()
    user_context.get_provider_tokens.assert_not_called()


def test_scrubs_expanded_secret_from_failure_text(probe):
    # A reference in the URL is not a secret field of the SDK model.
    probe.return_value = MCPTestFailure(
        error=f"404 Not Found for '{MCP_URL}?key=url-secret'",
        error_kind='connection',
    )
    client = _client(settings=None, user_context=_user_context({'KEY': 'url-secret'}))

    response = client.post(
        '/api/v1/mcp/test', json={'server': {'url': MCP_URL + '?key=${KEY}'}}
    )

    body = response.json()
    assert _probed_request(probe).resolved_server.url == MCP_URL + '?key=url-secret'
    assert 'url-secret' not in body['error']
    assert REDACTED in body['error']
