"""Native Git ownership, OAuth binding and rotation against migrated PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from ssl import SSLContext
from types import MappingProxyType
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import JsonValue, SecretStr, TypeAdapter
from sqlalchemy import select, update

from integrations import native_git_mixin
from integrations.bitbucket.bitbucket_service import SaaSBitBucketService
from integrations.github.github_service import SaaSGitHubService
from integrations.gitlab.gitlab_service import SaaSGitLabService
from openhands.app_server.integrations.service_types import ProviderType
from server.auth import token_manager
from server.auth.native_git_config import (
    NativeGitConfig,
    git_config,
    native_git_capabilities,
)
from server.auth.native_types import SessionFactory
from server.routes import native_git
from server.services import native_git_credentials
from server.services.native_auth_service import NativeLogin
from server.services.native_git_credentials import NativeGitCredentialService
from server.services.native_git_provider import (
    GitCredentialError,
    GitGrant,
    GitIdentity,
)
from storage.encrypt_utils import get_jwt_service
from storage.native_auth import AuthAccount, PasswordCredential
from storage.native_git import GitConnection, GitOAuthState
from storage.user import User
from tests.unit.server.auth import test_native_runtime as runtime_fixtures
from tests.unit.server.auth.native_test_types import (
    GitRuntime,
    NativeRuntime,
    present,
)

native_runtime = runtime_fixtures.native_runtime

IDENTITY = GitIdentity('1234', 'developer', 'Developer')


@pytest.fixture
async def git_runtime(
    native_runtime: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> AsyncIterator[GitRuntime]:
    auth_service, login, _ = native_runtime
    service = NativeGitCredentialService(async_session_maker)
    for module in (native_git_credentials, native_git, native_git_mixin):
        monkeypatch.setattr(module, 'get_native_git_service', lambda: service)
    monkeypatch.setattr(native_git, 'get_native_auth_service', lambda: auth_service)
    monkeypatch.setattr(token_manager, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(token_manager, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(native_git_mixin, 'ENABLE_KEYCLOAK', False)
    for provider in ('github', 'gitlab', 'bitbucket'):
        module = __import__(
            f'integrations.{provider}.{provider}_service', fromlist=['ENABLE_KEYCLOAK']
        )
        monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
        monkeypatch.setenv(f'NATIVE_GIT_{provider.upper()}_OAUTH_ENABLED', '1')
        monkeypatch.setenv(f'{provider.upper()}_APP_CLIENT_ID', 'test-client')
        monkeypatch.setenv(f'{provider.upper()}_APP_CLIENT_SECRET', 'test-secret')
    monkeypatch.setattr(native_git_credentials, 'revoke_grant', AsyncMock())
    from unittest.mock import MagicMock

    from server.auth import gitlab_sync

    monkeypatch.setattr(gitlab_sync, 'schedule_gitlab_repo_sync', MagicMock())
    verify = AsyncMock(return_value=IDENTITY)
    monkeypatch.setattr(native_git_credentials, 'verify_provider_identity', verify)
    yield service, login, verify


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,email',
    [('github', None), ('gitlab', None), ('bitbucket', 'dev@example.com')],
)
async def test_manual_encrypted_and_sdk_round_trip(
    git_runtime: GitRuntime,
    async_session_maker: SessionFactory,
    provider: str,
    email: str | None,
) -> None:
    service, login, verify = git_runtime
    account = login.principal.account_id
    result = await service.connect_manual(
        account, provider, 'private-provider-token', email=email
    )
    assert result['account']['id'] == IDENTITY.id
    assert result['auth_type'] == ('api_token' if email else 'pat')
    assert 'private-provider-token' not in str(result)
    token = await service.get_token(account, ProviderType(provider))
    assert (
        present(token.token).get_secret_value()
        == ('dev@example.com:' if email else '') + 'private-provider-token'
    )
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert 'private-provider-token' not in present(
            present(row).encrypted_access_token
        )
        assert present(row).encrypted_refresh_token is None
    assert (
        await token_manager.TokenManager().get_idp_token_by_user_id(
            str(account), ProviderType(provider)
        )
        == present(token.token).get_secret_value()
    )
    await service.disconnect(account, provider)
    assert (await service.list_connections(account))['connections'] == []
    assert await native_git.get_native_auth_service().authenticate_session(login.token)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['github', 'gitlab', 'bitbucket'])
async def test_oauth_pkce_exchange_cancel_and_replay(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
    provider: str,
) -> None:
    service, login, _ = git_runtime
    exchange = AsyncMock(
        return_value=GitGrant(
            'oauth-access', 'rotating-refresh', datetime.now(UTC) + timedelta(hours=1)
        )
    )
    monkeypatch.setattr(native_git_credentials, 'exchange_grant', exchange)
    url = await service.start_oauth(login.principal, provider)
    params = parse_qs(urlsplit(url).query)
    assert params['redirect_uri'] == [
        f'https://native.example.com/oauth/git/{provider}/callback'
    ]
    assert ('code_challenge' in params) == (provider != 'bitbucket')
    state = params['state'][0]
    async with async_session_maker() as session:
        record = await session.scalar(select(GitOAuthState))
        assert present(record).token_digest != state
        assert present(record).session_id == login.principal.session_id
    assert (
        await service.complete_oauth(login.principal, provider, state, 'provider-code')
        == 'connected'
    )
    exchange.assert_awaited_once()
    with pytest.raises(GitCredentialError, match='oauth_state_invalid'):
        await service.complete_oauth(login.principal, provider, state, 'provider-code')
    params = parse_qs(
        urlsplit(await service.start_oauth(login.principal, provider)).query
    )
    assert (
        await service.complete_oauth(
            login.principal, provider, params['state'][0], None, 'access_denied'
        )
        == 'cancelled'
    )
    assert (
        len((await service.list_connections(login.principal.account_id))['connections'])
        == 1
    )


@pytest.mark.asyncio
async def test_oauth_wrong_session_expired_state_and_disconnect(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = git_runtime
    params = parse_qs(
        urlsplit(await service.start_oauth(login.principal, 'github')).query
    )
    from dataclasses import replace

    wrong_session = replace(login.principal, session_id=uuid4())
    with pytest.raises(GitCredentialError, match='browser_session_required'):
        await service.complete_oauth(
            wrong_session, 'github', params['state'][0], 'code'
        )
    await service.disconnect(login.principal.account_id, 'github')
    with pytest.raises(GitCredentialError, match='oauth_state_invalid'):
        await service.complete_oauth(
            login.principal, 'github', params['state'][0], 'code'
        )
    params = parse_qs(
        urlsplit(await service.start_oauth(login.principal, 'github')).query
    )
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(GitOAuthState).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
    with pytest.raises(GitCredentialError, match='oauth_state_invalid'):
        await service.complete_oauth(
            login.principal, 'github', params['state'][0], 'code'
        )


@pytest.mark.asyncio
async def test_connection_conflicts_and_active_actor_lookup(
    git_runtime: GitRuntime, async_session_maker: SessionFactory
) -> None:
    service, login, verify = git_runtime
    account = login.principal.account_id
    await service.connect_manual(account, 'github', 'first-token')
    verify.return_value = replace(IDENTITY, id='different')
    with pytest.raises(GitCredentialError, match='provider_account_conflict'):
        await service.connect_manual(account, 'github', 'second-token')
    verify.return_value = IDENTITY
    second = uuid4()
    async with async_session_maker() as session, session.begin():
        user = await session.get(User, account)
        session.add(
            AuthAccount(
                id=second,
                normalized_email='second@example.com',
                display_email='second@example.com',
            )
        )
        await session.flush()
        session.add(
            PasswordCredential(
                account_id=second,
                normalized_login_email='second@example.com',
                display_email='second@example.com',
                password_hash='not-used',
            )
        )
        session.add(User(id=second, current_org_id=present(user).current_org_id))
    with pytest.raises(GitCredentialError, match='provider_account_conflict'):
        await service.connect_manual(second, 'github', 'second-token')
    assert await service.resolve_actor(
        ProviderType.GITHUB, 'github.com', IDENTITY.id
    ) == str(account)
    verify.return_value = replace(IDENTITY, id='different')
    assert (
        await service.resolve_actor(ProviderType.GITHUB, 'github.com', IDENTITY.id)
        is None
    )
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User).where(User.id == account).values(is_disabled=True)
        )
    assert (
        await service.resolve_actor(ProviderType.GITHUB, 'github.com', IDENTITY.id)
        is None
    )
    with pytest.raises(GitCredentialError, match='account_unavailable'):
        await service.get_token(account, ProviderType.GITHUB)


async def expired_oauth(
    service: NativeGitCredentialService,
    login: NativeLogin,
    async_session_maker: SessionFactory,
) -> None:
    await service.connect_manual(login.principal.account_id, 'gitlab', 'old-access')
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(GitConnection).values(
                auth_type='oauth',
                encrypted_refresh_token=get_jwt_service().encrypt_value('old-refresh'),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )


@pytest.mark.asyncio
async def test_concurrent_refresh_rotates_once_and_disconnect_wins(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = git_runtime
    await expired_oauth(service, login, async_session_maker)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def exchange(
        config: NativeGitConfig,
        *,
        code: str | None = None,
        verifier: str | None = None,
        refresh_token: str | None = None,
    ) -> GitGrant:
        entered.set()
        await release.wait()
        return GitGrant(
            'new-access', 'new-refresh', datetime.now(UTC) + timedelta(hours=1)
        )

    refresh = AsyncMock(side_effect=exchange)
    monkeypatch.setattr(native_git_credentials, 'exchange_grant', refresh)
    first = asyncio.create_task(
        service.get_token(login.principal.account_id, ProviderType.GITLAB)
    )
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(
        service.get_token(login.principal.account_id, ProviderType.GITLAB)
    )
    release.set()
    results = await asyncio.gather(first, second)
    assert [present(t.token).get_secret_value() for t in results] == [
        'new-access',
        'new-access',
    ]
    assert refresh.await_count == 1
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(GitConnection).values(
                expires_at=datetime.now(UTC) - timedelta(minutes=1)
            )
        )
    release.clear()
    entered.clear()
    first = asyncio.create_task(
        service.get_token(login.principal.account_id, ProviderType.GITLAB)
    )
    await asyncio.wait_for(entered.wait(), 5)
    disconnect = asyncio.create_task(
        service.disconnect(login.principal.account_id, 'gitlab')
    )
    release.set()
    await asyncio.gather(first, disconnect)
    with pytest.raises(GitCredentialError, match='provider_not_connected'):
        await service.get_token(login.principal.account_id, ProviderType.GITLAB)
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert (
            present(row).encrypted_access_token
            is present(row).encrypted_refresh_token
            is None
        )


@pytest.mark.asyncio
async def test_refresh_preserves_existing_refresh_when_provider_omits_replacement(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = git_runtime
    await expired_oauth(service, login, async_session_maker)
    monkeypatch.setattr(
        native_git_credentials,
        'exchange_grant',
        AsyncMock(
            return_value=GitGrant(
                'new-access', expires_at=datetime.now(UTC) + timedelta(hours=1)
            )
        ),
    )
    result = await service.get_token(login.principal.account_id, ProviderType.GITLAB)
    assert present(result.token).get_secret_value() == 'new-access'
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert (
            get_jwt_service().decrypt_value(present(row).encrypted_refresh_token)
            == 'old-refresh'
        )


@pytest.mark.asyncio
async def test_provider_outage_preserves_encrypted_rotation_and_app_session(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, verify = git_runtime
    await expired_oauth(service, login, async_session_maker)
    monkeypatch.setattr(
        native_git_credentials,
        'exchange_grant',
        AsyncMock(
            return_value=GitGrant(
                'rotated-access',
                'rotated-refresh',
                datetime.now(UTC) + timedelta(hours=1),
            )
        ),
    )
    verify.side_effect = GitCredentialError('provider_unavailable', 503)
    with pytest.raises(GitCredentialError, match='provider_unavailable'):
        await service.get_token(login.principal.account_id, ProviderType.GITLAB)
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert (
            get_jwt_service().decrypt_value(present(row).encrypted_refresh_token)
            == 'rotated-refresh'
        )
        assert present(row).revoked_at is None
    assert await native_git.get_native_auth_service().authenticate_session(login.token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,service_class',
    [
        ('github', SaaSGitHubService),
        ('gitlab', SaaSGitLabService),
        ('bitbucket', SaaSBitBucketService),
    ],
)
async def test_saas_service_headers_use_current_native_connection(
    git_runtime: GitRuntime,
    provider: str,
    service_class: type[SaaSGitHubService]
    | type[SaaSGitLabService]
    | type[SaaSBitBucketService],
) -> None:
    service, login, _ = git_runtime
    email = 'dev@example.com' if provider == 'bitbucket' else None
    await service.connect_manual(
        login.principal.account_id, provider, 'valid-token', email=email
    )
    adapter = service_class(external_auth_id=str(login.principal.account_id))
    headers = await adapter._get_headers()
    assert headers['Authorization'].startswith('Basic ' if email else 'Bearer ')
    await service.disconnect(login.principal.account_id, provider)
    with pytest.raises(GitCredentialError, match='provider_not_connected'):
        await adapter._get_headers()


@pytest.mark.asyncio
async def test_callback_redirect_redacts_provider_error_and_wrong_session(
    git_runtime: GitRuntime,
) -> None:
    _, login, _ = git_runtime
    app = FastAPI()
    app.include_router(native_git.native_git_oauth_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://native.example.com'
    ) as client:
        response = await client.get(
            '/oauth/git/github/callback',
            params={'error': 'secret-token-must-not-appear', 'state': 'a' * 40},
        )
    assert response.status_code == 303
    assert 'secret-token' not in response.headers['location']
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['location'].startswith('/settings/integrations?')


def test_operator_hosts_and_unapproved_input(
    git_runtime: GitRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('GITLAB_HOST', 'gitlab.example.com')
    assert git_config('gitlab').host == 'gitlab.example.com'
    assert git_config('gitlab').api_url == 'https://gitlab.example.com/api/v4'
    assert native_git_capabilities()['gitlab']['hosts'] == ['gitlab.example.com']
    for host in (
        'attacker.test',
        'https://gitlab.example.com@attacker.test',
        'http://gitlab.example.com',
        'gitlab.example.com/evil',
    ):
        with pytest.raises(ValueError):
            git_config('gitlab', host)


@pytest.mark.asyncio
async def test_unverified_rotation_is_never_exposed_until_identity_recovers(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, verify = git_runtime
    await expired_oauth(service, login, async_session_maker)
    exchange = AsyncMock(
        return_value=GitGrant(
            'new-access', 'new-refresh', datetime.now(UTC) + timedelta(hours=1)
        )
    )
    monkeypatch.setattr(native_git_credentials, 'exchange_grant', exchange)
    verify.side_effect = GitCredentialError('provider_unavailable', 503)
    for _ in range(2):
        with pytest.raises(GitCredentialError, match='provider_unavailable'):
            await service.get_token(login.principal.account_id, ProviderType.GITLAB)
    assert exchange.await_count == 1
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert not present(row).identity_verified
        assert (
            get_jwt_service().decrypt_value(present(row).encrypted_refresh_token)
            == 'new-refresh'
        )
    verify.side_effect = None
    verify.return_value = IDENTITY
    assert (
        present(
            (
                await service.get_token(login.principal.account_id, ProviderType.GITLAB)
            ).token
        ).get_secret_value()
        == 'new-access'
    )


@pytest.mark.asyncio
async def test_actor_reconnect_same_subject_on_different_host_denied(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, verify = git_runtime
    monkeypatch.setenv('NATIVE_GIT_GITLAB_HOSTS', 'gitlab.com,gitlab.other.example')
    await service.connect_manual(login.principal.account_id, 'gitlab', 'first')

    async def move_account(
        config: NativeGitConfig,
        token: str,
        email: str | None = None,
        *,
        manual: bool = False,
    ) -> GitIdentity:
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(GitConnection).values(
                    host='gitlab.other.example', generation=GitConnection.generation + 1
                )
            )
        return IDENTITY

    verify.side_effect = move_account
    assert (
        await service.resolve_actor(ProviderType.GITLAB, 'gitlab.com', IDENTITY.id)
        is None
    )


@pytest.mark.asyncio
async def test_pending_connect_does_not_resurrect_after_disconnect(
    git_runtime: GitRuntime,
) -> None:
    service, login, verify = git_runtime
    entered, released = asyncio.Event(), asyncio.Event()

    async def check(
        config: NativeGitConfig,
        token: str,
        email: str | None = None,
        *,
        manual: bool = False,
    ) -> GitIdentity:
        entered.set()
        await released.wait()
        return IDENTITY

    verify.side_effect = check
    pending = asyncio.create_task(
        service.connect_manual(login.principal.account_id, 'github', 'pending-token')
    )
    await asyncio.wait_for(entered.wait(), 5)
    await service.disconnect(login.principal.account_id, 'github')
    released.set()
    with pytest.raises(GitCredentialError, match='connection_changed'):
        await pending


@pytest.mark.asyncio
async def test_native_v1_secrets_adapter_and_sdk_resolve_same_connection(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    from pydantic import SecretStr

    from openhands.app_server.integrations.provider import ProviderToken
    from openhands.app_server.secrets.secrets_router import (
        store_provider_tokens,
        unset_provider_tokens,
    )
    from openhands.app_server.settings.settings_models import POSTProviderModel
    from openhands.app_server.user.auth_user_context import AuthUserContext
    from server.auth.saas_user_auth import SaasUserAuth
    from storage import saas_secrets_store

    service, login, _ = git_runtime
    monkeypatch.setattr(saas_secrets_store, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(saas_secrets_store, 'a_session_maker', async_session_maker)
    store = saas_secrets_store.SaasSecretsStore(
        str(login.principal.account_id), get_jwt_service()
    )
    await store_provider_tokens(
        POSTProviderModel(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(token=SecretStr('sdk-git-token'))
            }
        ),
        secrets_store=store,
        provider_tokens={},
        user_id=str(login.principal.account_id),
    )
    auth = await SaasUserAuth.get_for_user(str(login.principal.account_id))
    context = AuthUserContext(user_auth=auth)
    assert await context.get_latest_token(ProviderType.GITHUB) == 'sdk-git-token'
    assert (
        TypeAdapter(dict[str, str]).validate_python(
            await context.get_provider_tokens(as_env_vars=True)
        )['github_token']
        == 'sdk-git-token'
    )
    assert (
        present(
            (present(await store.load())).provider_tokens[ProviderType.GITHUB].token
        ).get_secret_value()
        == 'sdk-git-token'
    )
    await unset_provider_tokens(
        secrets_store=store, user_id=str(login.principal.account_id)
    )
    assert (await service.list_connections(login.principal.account_id))[
        'connections'
    ] == []


@pytest.mark.asyncio
async def test_oauth_mixed_credential_identity_and_invalid_header_denied(
    git_runtime: GitRuntime,
    native_runtime: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.auth import saas_user_auth
    from server.auth.saas_user_auth import SaasUserAuth

    _, login, _ = git_runtime
    request = Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/api/git-connections/github/oauth',
            'headers': [
                (b'cookie', f'openhands_session={login.token}'.encode()),
                (b'authorization', b'Bearer other-account-key'),
            ],
        }
    )
    monkeypatch.setattr(
        saas_user_auth,
        'saas_user_auth_from_bearer',
        AsyncMock(
            return_value=SaasUserAuth(user_id=str(uuid4()), refresh_token=SecretStr(''))
        ),
    )
    with pytest.raises(GitCredentialError, match='browser_identity_mismatch'):
        await native_git.browser_principal(request)
    monkeypatch.setattr(
        saas_user_auth, 'saas_user_auth_from_bearer', AsyncMock(return_value=None)
    )
    with pytest.raises(GitCredentialError, match='browser_identity_mismatch'):
        await native_git.browser_principal(request)


@pytest.mark.asyncio
async def test_native_pat_repositories_do_not_require_app_installation(
    git_runtime: GitRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.types import AppMode

    service, login, _ = git_runtime
    await service.connect_manual(login.principal.account_id, 'github', 'pat-token')
    adapter = SaaSGitHubService(external_auth_id=str(login.principal.account_id))
    request = AsyncMock(
        return_value=([{'id': 42, 'full_name': 'developer/repo', 'private': True}], {})
    )
    monkeypatch.setattr(adapter, '_make_request', request)
    monkeypatch.setattr(adapter, '_get_external_auth_id', AsyncMock(return_value=None))
    repositories = await adapter.get_all_repositories('pushed', AppMode.SAAS)
    assert repositories[0].full_name == 'developer/repo'
    assert request.call_args.args[0] == 'https://api.github.com/user/repos'
    assert await adapter.get_installations() == []


@pytest.mark.asyncio
async def test_disabled_provider_can_disconnect_without_breaking_app(
    git_runtime: GitRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, login, _ = git_runtime
    await service.connect_manual(login.principal.account_id, 'github', 'pat-token')
    monkeypatch.setenv('NATIVE_GIT_GITHUB_MANUAL_ENABLED', 'false')
    monkeypatch.setenv('NATIVE_GIT_GITHUB_OAUTH_ENABLED', 'false')
    assert await service.get_provider_tokens(login.principal.account_id) == {}
    await service.disconnect(login.principal.account_id, 'github')
    assert await native_git.get_native_auth_service().authenticate_session(login.token)


@pytest.mark.asyncio
async def test_provider_401_requires_reconnect_without_erasing_credentials(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = git_runtime
    await service.connect_manual(login.principal.account_id, 'github', 'revoked-pat')
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={'message': 'Bad credentials'})
    )

    def provider_client(
        *, timeout: float, follow_redirects: bool, verify: SSLContext | str | bool
    ) -> httpx.AsyncClient:
        return original(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            transport=transport,
        )

    monkeypatch.setattr(native_git_mixin.httpx, 'AsyncClient', provider_client)
    adapter = SaaSGitHubService(external_auth_id=str(login.principal.account_id))
    with pytest.raises(GitCredentialError, match='credential_rejected'):
        await adapter._make_request('https://api.github.com/user')
    connection = (await service.list_connections(login.principal.account_id))[
        'connections'
    ][0]
    assert connection['status'] == 'reconnect_required'
    async with async_session_maker() as session:
        row = await session.scalar(select(GitConnection))
        assert present(row).encrypted_access_token is not None
    assert await native_git.get_native_auth_service().authenticate_session(login.token)
    await service.connect_manual(login.principal.account_id, 'github', 'new-pat')
    await service.mark_rejected_by_authorization(
        str(login.principal.account_id), 'github', 'Bearer revoked-pat'
    )
    assert (await service.list_connections(login.principal.account_id))['connections'][
        0
    ]['status'] == 'connected'


@pytest.mark.asyncio
async def test_default_host_webhook_row_cannot_use_alternate_host(
    git_runtime: GitRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    from integrations.gitlab.webhook_installation import verify_webhook_conditions
    from integrations.types import GitLabResourceType
    from storage.gitlab_webhook import GitlabWebhook

    service, login, _ = git_runtime
    monkeypatch.setenv('NATIVE_GIT_GITLAB_HOSTS', 'gitlab.com,git.other.example')
    await service.connect_manual(login.principal.account_id, 'gitlab', 'first')
    row = GitlabWebhook(
        project_id='1234', user_id=str(login.principal.account_id), webhook_exists=False
    )
    await service.disconnect(login.principal.account_id, 'gitlab')
    await service.connect_manual(
        login.principal.account_id, 'gitlab', 'second', host='git.other.example'
    )
    adapter = SaaSGitLabService(external_auth_id=str(login.principal.account_id))
    call = AsyncMock()
    monkeypatch.setattr(adapter, '_make_request', call)
    with pytest.raises(GitCredentialError, match='webhooks_unsupported_host'):
        await verify_webhook_conditions(
            adapter, GitLabResourceType.PROJECT, '1234', MagicMock(), row
        )
    call.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,service_class',
    [('github', SaaSGitHubService), ('gitlab', SaaSGitLabService)],
)
async def test_alternate_host_repository_branch_clone_and_pr_requests(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    service_class: type[SaaSGitHubService] | type[SaaSGitLabService],
) -> None:
    from openhands.app_server.integrations.provider import ProviderHandler
    from openhands.app_server.integrations.service_types import RequestMethod

    service, login, _ = git_runtime
    monkeypatch.setenv('ENABLE_KEYCLOAK', 'false')
    monkeypatch.setenv(
        f'NATIVE_GIT_{provider.upper()}_HOSTS', f'{provider}.com,git.other.example'
    )
    await service.connect_manual(
        login.principal.account_id,
        provider,
        'host-bound-token',
        host='git.other.example',
    )
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == 'git.other.example'
        assert request.headers['Authorization'] == 'Bearer host-bound-token'
        if request.url.path.endswith('/branches'):
            return httpx.Response(
                200,
                json=[{'name': 'main', 'commit': {'sha': 'abc'}, 'protected': False}],
            )
        if request.method == 'POST':
            return httpx.Response(201, json={'id': 1})
        return httpx.Response(
            200,
            json={
                'id': 42,
                'full_name': 'developer/repo',
                'path_with_namespace': 'developer/repo',
                'visibility': 'private',
                'private': True,
                'default_branch': 'main',
                'permissions': {'push': True},
                'owner': {'type': 'User'},
            },
        )

    original = httpx.AsyncClient

    def provider_client(
        *, timeout: float, follow_redirects: bool, verify: SSLContext | str | bool
    ) -> httpx.AsyncClient:
        return original(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            transport=httpx.MockTransport(handle),
        )

    monkeypatch.setattr(native_git_mixin.httpx, 'AsyncClient', provider_client)
    adapter = service_class(external_auth_id=str(login.principal.account_id))
    repository = await adapter.get_repository_details_from_repo_name('developer/repo')
    assert repository.full_name == 'developer/repo'
    assert adapter.GRAPHQL_URL == 'https://git.other.example/api/graphql'
    branches = await adapter.get_branches('developer/repo')
    assert branches[0].name == 'main'
    tokens = await service.get_provider_tokens(login.principal.account_id)
    handler = ProviderHandler(
        provider_tokens=MappingProxyType(tokens),
        external_auth_id=str(login.principal.account_id),
    )
    clone_url = await handler.get_authenticated_git_url(
        'developer/repo', specified_provider=ProviderType(provider)
    )
    assert urlsplit(clone_url).hostname == 'git.other.example'
    assert 'host-bound-token' in clone_url
    await adapter._make_request(
        adapter.BASE_URL + '/projects/42/merge_requests'
        if provider == 'gitlab'
        else adapter.BASE_URL + '/repos/developer/repo/pulls',
        {'title': 'Review this change'},
        RequestMethod.POST,
    )
    assert requests[-1].method == 'POST'
    explicit_wrong = service_class(
        external_auth_id=str(login.principal.account_id), base_domain=f'{provider}.com'
    )
    with pytest.raises(GitCredentialError, match='provider_host_mismatch'):
        await explicit_wrong.get_latest_token()


@pytest.mark.asyncio
async def test_signed_github_authorization_revocation_and_shared_installations(
    git_runtime: GitRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    import hashlib
    import hmac
    import importlib
    import json
    from unittest.mock import patch

    from storage import database
    from storage.github_app_installation import GithubAppInstallation

    service, login, _ = git_runtime
    await service.connect_manual(login.principal.account_id, 'github', 'oauth-token')
    async with async_session_maker() as session, session.begin():
        await session.execute(update(GitConnection).values(auth_type='oauth'))
        session.add_all(
            [
                GithubAppInstallation(
                    installation_id='1', encrypted_token='encrypted-one'
                ),
                GithubAppInstallation(
                    installation_id='2', encrypted_token='encrypted-two'
                ),
            ]
        )
    with (
        patch('integrations.github.github_manager.GithubManager'),
        patch('integrations.github.data_collector.GitHubDataCollector'),
    ):
        routes = importlib.import_module('server.routes.integration.github')
    monkeypatch.setattr(routes, 'GITHUB_APP_WEBHOOK_SECRET', 'test-hook-secret')
    monkeypatch.setattr(routes, 'GITHUB_WEBHOOKS_ENABLED', True)
    monkeypatch.setattr(database, 'a_session_maker', async_session_maker)
    app = FastAPI()
    app.include_router(routes.github_integration_router)

    async def post(
        client: httpx.AsyncClient, event: str, payload: JsonValue
    ) -> httpx.Response:
        content = json.dumps(payload).encode()
        signature = (
            'sha256='
            + hmac.new(b'test-hook-secret', content, hashlib.sha256).hexdigest()
        )
        return await client.post(
            '/integration/github/events',
            content=content,
            headers={'X-GitHub-Event': event, 'X-Hub-Signature-256': signature},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://native.example.com'
    ) as client:
        response = await post(
            client,
            'github_app_authorization',
            {'action': 'revoked', 'sender': {'id': 1234}},
        )
        assert response.status_code == 200
        assert (await service.list_connections(login.principal.account_id))[
            'connections'
        ] == []
        response = await post(
            client, 'installation', {'action': 'suspend', 'installation': {'id': 1}}
        )
        assert response.status_code == 200
    async with async_session_maker() as session:
        rows = (await session.scalars(select(GithubAppInstallation))).all()
        assert [row.installation_id for row in rows] == ['2']
    assert await native_git.get_native_auth_service().authenticate_session(login.token)


@pytest.mark.asyncio
async def test_github_installation_is_lazy_and_accepts_app_id_without_oauth_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    user_id = str(uuid4())
    service = MagicMock()
    service.list_connections = AsyncMock(return_value={'connections': []})
    monkeypatch.setattr(native_git, 'get_native_git_service', lambda: service)
    monkeypatch.setenv('GITHUB_APP_ID', '12345')
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY', 'test-private-key')
    monkeypatch.delenv('GITHUB_APP_CLIENT_ID', raising=False)
    monkeypatch.setattr(
        native_git,
        'git_config',
        lambda _: type(
            'Config', (), {'api_url': 'https://api.github.com', 'host': 'github.com'}
        )(),
    )
    encode = MagicMock(return_value='app-assertion')
    request = AsyncMock(
        return_value=httpx.Response(200, json={'slug': 'native-example'})
    )
    monkeypatch.setattr(native_git.jwt, 'encode', encode)
    monkeypatch.setattr(native_git, 'provider_request', request)
    request.assert_not_called()
    result = await native_git.github_installation(user_id)
    assert result == {
        'installation_url': 'https://github.com/apps/native-example/installations/new'
    }
    assert encode.call_args.args[0]['iss'] == '12345'
    service.list_connections.assert_awaited_once_with(UUID(user_id))
    request.assert_awaited_once_with(
        'GET',
        'https://api.github.com/app',
        headers={
            'Authorization': 'Bearer app-assertion',
            'Accept': 'application/vnd.github+json',
        },
    )
