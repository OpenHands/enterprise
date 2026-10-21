"""With ``ENABLE_OAUTH_V2_LOGIN`` off, background git-token reads take the
Keycloak path exactly as before, even when an ``oauth_tokens`` row exists.
Runs against the test database. Keycloak is faked: tests have no Keycloak
server."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from integrations.azure_devops.azure_devops_manager import AzureDevOpsManager
from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from integrations.bitbucket.bitbucket_manager import BitbucketManager
from integrations.bitbucket.bitbucket_service import SaaSBitBucketService
from integrations.bitbucket_data_center.bitbucket_dc_manager import (
    BitbucketDCManager,
)
from integrations.bitbucket_data_center.bitbucket_dc_service import (
    SaaSBitbucketDCService,
)
from integrations.github.github_service import SaaSGitHubService
from integrations.gitlab.gitlab_service import SaaSGitLabService
from integrations.types import UserData
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderType,
)
from server.auth.constants import is_oauth_v2_login_enabled
from server.auth.gitlab_sync import _user_has_gitlab_provider, schedule_gitlab_repo_sync
from server.auth.saas_user_auth import get_user_auth_from_keycloak_id
from server.auth.token_manager import TokenManager
from storage.auth_tokens import AuthTokens

pytestmark = pytest.mark.usefixtures('oauth_v2_login_off')


def _keycloak(token_manager: TokenManager, token: str | None = 'from-keycloak'):
    """Fake the Keycloak offline-token path; returns the IdP-token fake."""
    return (
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value=token),
        ),
    )


@pytest.mark.parametrize(
    ('value', 'enabled'),
    [
        ('1', True),
        ('true', True),
        ('True', True),
        ('0', False),
        ('false', False),
        ('', False),
    ],
)
def test_the_flag_is_parsed_like_the_web_client_parses_it(
    monkeypatch: pytest.MonkeyPatch, value: str, enabled: bool
):
    monkeypatch.setenv('ENABLE_OAUTH_V2_LOGIN', value)

    assert is_oauth_v2_login_enabled() is enabled


@pytest.mark.asyncio
async def test_keycloak_wins_over_an_oauth_tokens_row(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')
    token_manager = TokenManager()
    load, from_offline = _keycloak(token_manager)

    with load, from_offline as from_keycloak:
        token = await token_manager.get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB
        )

    assert token == 'from-keycloak'
    from_keycloak.assert_awaited_once_with(
        offline_token='offline', idp=ProviderType.GITLAB
    )


@pytest.mark.asyncio
async def test_without_an_offline_token_an_oauth_tokens_row_is_not_used(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITLAB
    )

    assert token is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('service_cls', 'provider_type'),
    [
        (SaaSGitLabService, ProviderType.GITLAB),
        (SaaSGitHubService, ProviderType.GITHUB),
        (SaaSBitBucketService, ProviderType.BITBUCKET),
        (SaaSBitbucketDCService, ProviderType.BITBUCKET_DATA_CENTER),
        (SaaSAzureDevOpsService, ProviderType.AZURE_DEVOPS),
    ],
)
async def test_services_acting_for_a_user_read_keycloak(
    create_git_provider,
    store_oauth_token,
    create_user,
    service_cls,
    provider_type,
):
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, 'oauth-v2-token')
    service = service_cls(external_auth_id=str(user.id))
    load, from_offline = _keycloak(service.token_manager)

    with load, from_offline:
        token = await service.get_latest_token()

    assert token is not None
    assert token.get_secret_value() == 'from-keycloak'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'service_cls', [SaaSGitLabService, SaaSGitHubService, SaaSBitBucketService]
)
async def test_a_401_is_not_retried(create_user, service_cls):
    service = service_cls(external_auth_id=str(create_user().id))
    url = 'https://git.example.com/api/user'
    sent: list[dict] = []

    async def execute_request(client, url, headers, params, method):
        sent.append(dict(headers))
        return httpx.Response(401, request=httpx.Request('GET', url))

    load, from_offline = _keycloak(service.token_manager)
    with (
        patch.object(service, 'execute_request', side_effect=execute_request),
        load,
        from_offline,
        pytest.raises(AuthenticationError),
    ):
        await service._make_request(url)

    assert service.refresh is False
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_lookup_by_git_user_id_reads_keycloak(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')
    token_manager = TokenManager()
    load, from_offline = _keycloak(token_manager)

    with (
        patch.object(
            token_manager,
            'get_user_id_from_idp_user_id',
            AsyncMock(return_value=str(user.id)),
        ),
        load,
        from_offline,
    ):
        token = await token_manager.get_idp_token_from_idp_user_id(
            '4242', ProviderType.GITLAB
        )

    assert token == 'from-keycloak'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('manager_cls', 'provider_type'),
    [
        (BitbucketManager, ProviderType.BITBUCKET),
        (BitbucketDCManager, ProviderType.BITBUCKET_DATA_CENTER),
        (AzureDevOpsManager, ProviderType.AZURE_DEVOPS),
    ],
)
async def test_managers_start_jobs_with_the_keycloak_token(
    create_git_provider,
    store_oauth_token,
    create_user,
    manager_cls,
    provider_type,
):
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, 'oauth-v2-token')
    token_manager = TokenManager()
    manager = manager_cls(token_manager)
    # The view and conversation startup are out of scope; only the token
    # handed to the new conversation matters here.
    view = MagicMock(
        user_info=UserData(
            user_id='git-user', username='alice', keycloak_user_id=str(user.id)
        ),
        initialize_new_conversation=AsyncMock(return_value='conversation-id'),
        create_new_conversation=AsyncMock(),
    )
    load, from_offline = _keycloak(token_manager)

    with (
        patch(f'{manager_cls.__module__}.get_saas_user_auth', AsyncMock()),
        patch.object(manager, 'send_message', AsyncMock()),
        load,
        from_offline,
    ):
        await manager.start_job(view)

    provider_tokens = view.create_new_conversation.await_args.args[1]
    assert provider_tokens[provider_type].token.get_secret_value() == 'from-keycloak'


@pytest.mark.asyncio
async def test_background_provider_tokens_ignore_oauth_tokens(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')
    auth = await get_user_auth_from_keycloak_id(str(user.id))

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        tokens = await auth.get_provider_tokens()

    assert tokens == {}


@pytest.mark.asyncio
async def test_an_oauth_v2_gitlab_token_does_not_count_for_the_repo_sync(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat')

    assert await _user_has_gitlab_provider(str(user.id)) is False


@pytest.mark.asyncio
async def test_a_keycloak_gitlab_token_counts_for_the_repo_sync(
    oauth_v2_stores, async_session_maker, create_user
):
    user_id = str(create_user().id)
    async with async_session_maker() as session:
        session.add(
            AuthTokens(
                keycloak_user_id=user_id,
                identity_provider=ProviderType.GITLAB.value,
                access_token='a',
                refresh_token='r',
                access_token_expires_at=0,
                refresh_token_expires_at=0,
            )
        )
        await session.commit()

    assert await _user_has_gitlab_provider(user_id) is True


@pytest.mark.asyncio
async def test_the_repo_sync_skips_a_user_with_only_an_oauth_v2_gitlab_token(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat')
    list_repositories = AsyncMock()

    before = asyncio.all_tasks()
    with patch.object(SaaSGitLabService, 'get_all_repositories', new=list_repositories):
        schedule_gitlab_repo_sync(str(user.id))
        await asyncio.gather(*(asyncio.all_tasks() - before))

    list_repositories.assert_not_awaited()
