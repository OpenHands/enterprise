"""Git-info keeps native app ownership across real routes and provider failures."""

from collections.abc import AsyncIterator
from ssl import SSLContext

import httpx
import pytest
from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import select

from integrations import native_git_mixin
from integrations.github import github_service
from openhands.app_server.user import user_router
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.native_session import csrf_for_token
from server.auth.native_types import SessionFactory
from server.routes import native_git
from server.services import native_git_credentials, native_git_provider
from server.services.native_git_credentials import NativeGitCredentialService
from storage.native_git import GitConnection
from tests.unit.server.auth.native_test_types import (
    NativeGitInfo,
    NativeRuntime,
    ProviderState,
    present,
)
from tests.unit.server.auth.test_native_runtime import ORIGIN, native_app

pytest_plugins = ['tests.unit.server.auth.test_native_runtime']


@pytest.fixture
async def native_git_info(
    native_runtime: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> AsyncIterator[NativeGitInfo]:
    _, login, _ = native_runtime
    service = NativeGitCredentialService(async_session_maker)
    for module in (native_git_credentials, native_git, native_git_mixin):
        monkeypatch.setattr(module, 'get_native_git_service', lambda: service)
    monkeypatch.setenv('ENABLE_KEYCLOAK', 'false')
    for module, provider, class_name, default_host in (
        (github_service, 'GITHUB', 'SaaSGitHubService', 'github.com'),
    ):
        monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
        monkeypatch.setenv(
            f'OPENHANDS_{provider}_SERVICE_CLS', f'{module.__name__}.{class_name}'
        )
        monkeypatch.setenv(f'NATIVE_GIT_{provider}_MANUAL_ENABLED', 'true')
        monkeypatch.setenv(f'NATIVE_GIT_{provider}_OAUTH_ENABLED', 'false')
        hosts = default_host
        hosts += f',{provider.lower()}.alternate.example'
        monkeypatch.setenv(f'NATIVE_GIT_{provider}_HOSTS', hosts)
    monkeypatch.setattr(native_git_mixin, 'ENABLE_KEYCLOAK', False)
    upstream = ProviderState()

    def respond(request: httpx.Request) -> httpx.Response:
        upstream.requests.append(str(request.url))
        path = request.url.path
        data: JsonValue
        if path.endswith('/user'):
            data = {
                'id': 31415,
                'uuid': '31415',
                'account_id': '31415',
                'login': 'git-user',
                'username': 'git-user',
                'nickname': 'git-user',
                'name': 'Git User',
                'display_name': 'Git User',
                'email': 'git-user@example.test',
                'avatar_url': '',
            }
        elif path.endswith('/personal_access_tokens/self'):
            data = {'scopes': ['api']}
        elif path.endswith('/user/emails'):
            data = {'values': []}
        elif path.endswith('/user/permissions/repositories'):
            data = {'values': []}
        elif path.endswith(('/projects', '/groups')):
            data = []
        else:
            raise AssertionError(f'Unexpected provider path: {path}')
        return httpx.Response(
            upstream.status, json=data, headers={'X-OAuth-Scopes': 'repo'}
        )

    class ProviderHttpx:
        BasicAuth = httpx.BasicAuth
        HTTPError = httpx.HTTPError
        HTTPStatusError = httpx.HTTPStatusError

        @staticmethod
        def AsyncClient(
            *,
            timeout: float,
            follow_redirects: bool,
            verify: SSLContext | str | bool = True,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=follow_redirects,
                verify=verify,
                transport=httpx.MockTransport(respond),
            )

    for module in (native_git_provider, native_git_mixin):
        monkeypatch.setattr(module, 'httpx', ProviderHttpx())
    app = native_app()
    app.include_router(user_router.router, prefix='/api/v1')
    app.include_router(native_git.native_git_router)

    async def context(request: Request) -> AuthUserContext:
        return AuthUserContext(user_auth=await get_user_auth(request))

    app.dependency_overrides[user_router.user_dependency.dependency] = context
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
        headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf_for_token(login.token)},
    ) as client:
        yield (client, upstream, login.principal.account_id)


async def connect(
    client: httpx.AsyncClient, provider: str, host: str | None = None
) -> None:
    body = {'token': 'git-info-test-token'}
    if host:
        body['host'] = host
    response = await client.put(f'/api/git-connections/{provider}', json=body)
    assert response.status_code == 200


@pytest.mark.parametrize('provider', ['github'])
@pytest.mark.parametrize('upstream_status', [200, 401, 503])
async def test_native_git_info_provider_status_keeps_app_identity(
    native_git_info: NativeGitInfo,
    async_session_maker: SessionFactory,
    provider: str,
    upstream_status: int,
) -> None:
    client, upstream, account_id = native_git_info
    await connect(client, provider)
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        encrypted_token, generation = (
            present(row).encrypted_access_token,
            present(row).generation,
        )
    upstream.requests.clear()
    upstream.status = upstream_status
    response = await client.get('/api/v1/users/git-info')
    expected_status = 422 if upstream_status == 401 else upstream_status
    assert response.status_code == expected_status
    if upstream_status == 200:
        assert response.json()['id'] == '31415'
        assert response.json()['id'] != str(account_id)
        assert response.json()['login'] == 'git-user'
    else:
        assert response.json()['error'] == (
            'credential_rejected' if upstream_status == 401 else 'provider_unavailable'
        )
    assert len([url for url in upstream.requests if url.endswith('/user')]) == 1
    connections = (await client.get('/api/git-connections')).json()['connections']
    assert connections[0]['status'] == (
        'reconnect_required' if upstream_status == 401 else 'connected'
    )
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert present(row).account_id == account_id
        assert present(row).encrypted_access_token == encrypted_token
        assert present(row).generation == generation
        assert present(row).revoked_at is None
    assert (await client.post('/api/authenticate')).status_code == 200
    if upstream_status == 503:
        upstream.status = 200
        assert (await client.get('/api/v1/users/git-info')).status_code == 200


@pytest.mark.parametrize('provider', ['github'])
async def test_native_git_info_uses_connected_approved_host(
    native_git_info: NativeGitInfo, provider: str
) -> None:
    client, upstream, _ = native_git_info
    host = f'{provider}.alternate.example'
    await connect(client, provider, host)
    upstream.requests.clear()
    response = await client.get('/api/v1/users/git-info')
    assert response.status_code == 200
    assert response.json()['id'] == '31415'
    version = 'v3'
    assert upstream.requests == [f'https://{host}/api/{version}/user']


async def test_native_git_info_without_connection_preserves_login(
    native_git_info: NativeGitInfo,
) -> None:
    client, upstream, _ = native_git_info
    response = await client.get('/api/v1/users/git-info')
    assert response.status_code == 403
    assert response.json()['detail'] == 'Git provider not connected'
    assert upstream.requests == []
    assert (await client.post('/api/authenticate')).status_code == 200
