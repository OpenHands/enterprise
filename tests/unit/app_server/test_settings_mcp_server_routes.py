"""``POST/PATCH/DELETE /api/v1/settings/mcp/{settings_key}`` on a real file store.

Each route changes one personal MCP server and must keep every sibling, and its
credentials, exactly as stored — also when several mutations run at once.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.file_store.memory import InMemoryFileStore
from openhands.app_server.settings.file_settings_store import FileSettingsStore
from openhands.app_server.settings.mcp_server_mutations import (
    MCPServerAlreadyExistsError,
    MCPServerNotFoundError,
    create_mcp_server,
    delete_mcp_server,
)
from openhands.app_server.settings.settings_router import router
from openhands.app_server.user_auth import (
    get_user_settings_store,
    reject_service_account,
)
from openhands.app_server.utils.dependencies import check_session_api_key

REDACTED = '**********'
OAUTH_STATE = {
    'tokens': {'access_token': 'oauth-access', 'refresh_token': 'oauth-refresh'},
    'token_expires_at': 123.0,
}


@pytest.fixture
def store() -> FileSettingsStore:
    return FileSettingsStore(InMemoryFileStore())


@pytest.fixture
def client(store: FileSettingsStore) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[check_session_api_key] = lambda: None
    app.dependency_overrides[reject_service_account] = lambda: None
    app.dependency_overrides[get_user_settings_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


async def _stored(store: FileSettingsStore) -> dict:
    settings = await store.load()
    assert settings is not None
    return {
        name: server.model_dump(
            mode='json', context={'expose_secrets': 'plaintext'}, exclude_none=True
        )
        for name, server in settings.agent_settings.mcp_config.items()
    }


def _seed(client: TestClient) -> None:
    for key, server in {
        'github': {
            'url': 'https://github.example/mcp',
            'auth': {'strategy': 'bearer', 'value': 'gh-token'},
        },
        'linear': {
            'url': 'https://linear.example/mcp',
            'auth': {'strategy': 'oauth2', 'state': OAUTH_STATE},
        },
        'local': {'command': 'uvx', 'args': ['mcp-fetch'], 'env': {'KEY': 'env-v'}},
    }.items():
        assert client.post(f'/api/v1/settings/mcp/{key}', json=server).status_code == (
            201
        )


@pytest.mark.asyncio
async def test_create_keeps_siblings_and_their_credentials(client, store):
    _seed(client)

    response = client.post(
        '/api/v1/settings/mcp/jira',
        json={
            'url': 'https://jira.example/mcp',
            'headers': {'X-Api-Key': 'jira-key'},
        },
    )

    assert response.status_code == 201
    body = response.json()['mcp_config']
    assert body['jira']['headers'] == {'X-Api-Key': REDACTED}
    assert 'jira-key' not in response.text
    stored = await _stored(store)
    assert list(stored) == ['github', 'linear', 'local', 'jira']
    assert stored['github']['auth'] == {'strategy': 'bearer', 'value': 'gh-token'}
    assert stored['linear']['auth']['state']['tokens']['refresh_token'] == (
        'oauth-refresh'
    )
    assert stored['local']['env'] == {'KEY': 'env-v'}
    assert stored['jira']['headers'] == {'X-Api-Key': 'jira-key'}


@pytest.mark.asyncio
async def test_create_conflict_is_rejected_without_writing(client, store):
    _seed(client)

    response = client.post(
        '/api/v1/settings/mcp/github',
        json={'url': 'https://other.example/mcp'},
    )

    assert response.status_code == 409
    assert (await _stored(store))['github']['url'] == 'https://github.example/mcp'


@pytest.mark.asyncio
async def test_patch_is_sparse_and_keeps_omitted_and_redacted_credentials(
    client, store
):
    _seed(client)

    response = client.patch(
        '/api/v1/settings/mcp/github',
        json={
            'description': 'GitHub tools',
            'enabled': False,
            'auth': {'strategy': 'bearer', 'value': REDACTED},
        },
    )
    linear = client.patch('/api/v1/settings/mcp/linear', json={'timeout': 30})

    assert response.status_code == 200
    assert linear.status_code == 200
    stored = await _stored(store)
    assert stored['github']['description'] == 'GitHub tools'
    assert stored['github']['enabled'] is False
    assert stored['github']['auth'] == {'strategy': 'bearer', 'value': 'gh-token'}
    # An OAuth server keeps its refreshed tokens through a non-auth edit.
    assert stored['linear']['timeout'] == 30
    assert stored['linear']['auth']['state'] == OAUTH_STATE


@pytest.mark.asyncio
async def test_patch_replaces_and_clears_credentials(client, store):
    _seed(client)

    replaced = client.patch(
        '/api/v1/settings/mcp/github',
        json={'auth': {'strategy': 'api_key', 'value': 'new-key'}},
    )
    cleared = client.patch('/api/v1/settings/mcp/local', json={'env': None})

    assert replaced.status_code == 200
    assert cleared.status_code == 200
    stored = await _stored(store)
    assert stored['github']['auth']['strategy'] == 'api_key'
    assert stored['github']['auth']['value'] == 'new-key'
    assert 'env' not in stored['local']


@pytest.mark.asyncio
async def test_patch_and_delete_of_missing_server_are_404(client, store):
    _seed(client)

    assert client.patch('/api/v1/settings/mcp/nope', json={}).status_code == 404
    assert client.delete('/api/v1/settings/mcp/nope').status_code == 404
    assert list(await _stored(store)) == ['github', 'linear', 'local']


def test_patch_rejects_unknown_fields(client):
    _seed(client)

    response = client.patch('/api/v1/settings/mcp/github', json={'bogus': 1})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_keeps_siblings(client, store):
    _seed(client)

    response = client.delete('/api/v1/settings/mcp/linear')

    assert response.status_code == 200
    assert set(response.json()['mcp_config']) == {'github', 'local'}
    stored = await _stored(store)
    assert stored['github']['auth']['value'] == 'gh-token'


@pytest.mark.asyncio
async def test_rename_with_redacted_credential_keeps_it(client, store):
    _seed(client)

    created = client.post(
        '/api/v1/settings/mcp/github-renamed',
        json={
            'url': 'https://github.example/mcp',
            'auth': {'strategy': 'bearer', 'value': REDACTED},
        },
    )
    deleted = client.delete('/api/v1/settings/mcp/github')

    assert created.status_code == 201
    assert deleted.status_code == 200
    stored = await _stored(store)
    assert 'github' not in stored
    assert stored['github-renamed']['auth'] == {
        'strategy': 'bearer',
        'value': 'gh-token',
    }


@pytest.mark.asyncio
async def test_concurrent_mutations_do_not_lose_updates(store):
    names = [f'server-{i}' for i in range(10)]

    await asyncio.gather(
        *(
            store.update_mcp_config(
                lambda servers, name=name: create_mcp_server(
                    servers, name, {'url': f'https://{name}.example/mcp'}
                )
            )
            for name in names
        )
    )
    await asyncio.gather(
        *(
            store.update_mcp_config(
                lambda servers, name=name: delete_mcp_server(servers, name)
            )
            for name in names[:5]
        )
    )

    assert sorted(await _stored(store)) == sorted(names[5:])


@pytest.mark.asyncio
async def test_failed_mutation_writes_nothing(store):
    await store.update_mcp_config(
        lambda servers: create_mcp_server(servers, 'a', {'url': 'https://a/mcp'})
    )

    with pytest.raises(MCPServerAlreadyExistsError):
        await store.update_mcp_config(
            lambda servers: create_mcp_server(servers, 'a', {'url': 'https://b/mcp'})
        )
    with pytest.raises(MCPServerNotFoundError):
        await store.update_mcp_config(lambda servers: delete_mcp_server(servers, 'b'))

    assert (await _stored(store))['a']['url'] == 'https://a/mcp'
