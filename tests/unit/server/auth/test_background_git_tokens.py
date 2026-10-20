"""Runs against the test database. Only the git providers' token endpoints
and, where a test says so, Keycloak are faked."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from integrations.bitbucket.bitbucket_service import SaaSBitBucketService
from integrations.bitbucket_data_center.bitbucket_dc_service import (
    SaaSBitbucketDCService,
)
from integrations.github.github_service import SaaSGitHubService
from integrations.gitlab.gitlab_service import SaaSGitLabService
from openhands.app_server.integrations.service_types import ProviderType
from server.auth.token_manager import TokenManager
from storage.oauth_token_store import OAuthTokenStore


def _token_endpoint(status_code: int = 200, body: dict | None = None) -> AsyncMock:
    response = MagicMock(
        status_code=status_code,
        headers={'content-type': 'application/json'},
        text='error',
    )
    response.json.return_value = body or {}
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__.return_value = client
    return client


@pytest.mark.asyncio
async def test_the_token_comes_from_oauth_tokens_without_keycloak(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITLAB
    )

    assert token == 'glpat-linked'


@pytest.mark.asyncio
async def test_an_expired_token_is_refreshed_at_the_provider(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(
        user.id,
        gitlab,
        'glpat-expired',
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    client = _token_endpoint(
        body={'access_token': 'glpat-fresh', 'refresh_token': 'r2', 'expires_in': 7200}
    )

    with patch('server.auth.oauth_v2_refresh.httpx.AsyncClient', return_value=client):
        token = await TokenManager().get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB
        )

    assert token == 'glpat-fresh'
    assert client.post.await_args.args[0] == 'https://gitlab.example.com/oauth/token'
    stored = await OAuthTokenStore(
        user_id=user.id, oauth_provider_id=gitlab.id
    ).get_valid_access_token()
    assert stored == 'glpat-fresh'


@pytest.mark.asyncio
async def test_without_a_token_or_keycloak_there_is_none(
    no_keycloak, create_git_provider, create_user
):
    await create_git_provider(ProviderType.GITLAB)

    token = await TokenManager().get_idp_token_for_user(
        str(create_user().id), ProviderType.GITLAB
    )

    assert token is None


@pytest.mark.asyncio
async def test_a_token_for_one_provider_does_not_answer_for_another(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    await create_git_provider(ProviderType.GITHUB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITHUB
    )

    assert token is None


@pytest.mark.asyncio
async def test_a_login_provider_token_is_not_a_git_token(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab_login = await create_git_provider(ProviderType.GITLAB, is_idp=True)
    user = create_user()
    await store_oauth_token(user.id, gitlab_login, 'login-session-token')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITLAB
    )

    assert token is None


@pytest.mark.asyncio
async def test_without_an_oauth_v2_token_keycloak_is_used(
    create_git_provider, create_user
):
    await create_git_provider(ProviderType.GITLAB)
    user_id = str(create_user().id)
    token_manager = TokenManager()

    with (
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='glpat-from-keycloak'),
        ) as from_keycloak,
    ):
        token = await token_manager.get_idp_token_for_user(user_id, ProviderType.GITLAB)

    assert token == 'glpat-from-keycloak'
    from_keycloak.assert_awaited_once_with(
        offline_token='offline', idp=ProviderType.GITLAB
    )


@pytest.mark.asyncio
async def test_a_failed_refresh_falls_back_to_keycloak_and_keeps_the_token(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(
        user.id,
        gitlab,
        'glpat-expired',
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    token_manager = TokenManager()

    with (
        patch(
            'server.auth.oauth_v2_refresh.httpx.AsyncClient',
            return_value=_token_endpoint(status_code=400),
        ),
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='glpat-from-keycloak'),
        ),
    ):
        token = await token_manager.get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB
        )

    assert token == 'glpat-from-keycloak'
    assert (
        await OAuthTokenStore(user_id=user.id, oauth_provider_id=gitlab.id).get_raw()
        is not None
    )


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
async def test_services_acting_for_a_user_read_oauth_tokens(
    no_keycloak,
    create_git_provider,
    store_oauth_token,
    create_user,
    service_cls,
    provider_type,
):
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, f'{provider_type.value}-token')

    token = await service_cls(external_auth_id=str(user.id)).get_latest_token()

    assert token is not None
    assert token.get_secret_value() == f'{provider_type.value}-token'
