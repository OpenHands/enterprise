"""GitLab access with the token in ``oauth_tokens``, without Keycloak.

Runs against the test database; any Keycloak call or Keycloak token read
fails the test.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.gitlab.gitlab_service import OAuthV2GitLabService
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.types import AppMode
from server.auth.gitlab_sync import schedule_oauth_v2_gitlab_repo_sync
from server.auth.oauth_v2_refresh import get_oauth_v2_git_token
from server.auth.token_manager import TokenManager
from storage.oauth_token_store import OAuthTokenStore


def _fail_keycloak(*args, **kwargs):
    raise AssertionError('Keycloak must not be used')


@pytest.fixture(autouse=True)
def no_keycloak():
    with (
        patch('server.auth.token_manager.get_keycloak_admin', _fail_keycloak),
        patch('server.auth.token_manager.get_keycloak_openid', _fail_keycloak),
        patch.object(TokenManager, 'get_idp_token', _fail_keycloak),
        patch.object(TokenManager, 'load_offline_token', _fail_keycloak),
        patch.object(TokenManager, 'get_idp_token_from_offline_token', _fail_keycloak),
    ):
        yield


@pytest.mark.asyncio
async def test_the_service_acts_with_the_oauth_v2_token(
    oauth_v2_gitlab_user_id, oauth_v2_gitlab_token
):
    service = await OAuthV2GitLabService.for_user(oauth_v2_gitlab_user_id)

    assert service is not None
    headers = await service._get_headers()
    assert headers['Authorization'] == f'Bearer {oauth_v2_gitlab_token}'
    latest = await service.get_latest_token()
    assert latest is not None
    assert latest.get_secret_value() == oauth_v2_gitlab_token


@pytest.mark.asyncio
async def test_no_service_for_a_user_without_a_token(gitlab_git_provider, create_user):
    assert await OAuthV2GitLabService.for_user(str(create_user().id)) is None


@pytest.mark.asyncio
async def test_no_service_without_a_gitlab_provider(oauth_v2_stores, create_user):
    assert await OAuthV2GitLabService.for_user(str(create_user().id)) is None


@pytest.mark.asyncio
async def test_the_gitlab_token_does_not_answer_for_other_providers(
    oauth_v2_gitlab_user_id,
):
    token = await get_oauth_v2_git_token(oauth_v2_gitlab_user_id, ProviderType.GITHUB)
    assert token is None


@pytest.mark.asyncio
async def test_an_expired_token_is_refreshed(gitlab_git_provider, create_user):
    user_id = create_user().id
    await OAuthTokenStore(
        user_id=user_id, oauth_provider_id=gitlab_git_provider.id
    ).store_tokens(
        access_token='glpat-expired',
        refresh_token='refresh',
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        refresh_token_expires_at=None,
    )
    # The provider's token endpoint is the only thing faked.
    response = MagicMock(status_code=200, headers={'content-type': 'application/json'})
    response.json.return_value = {
        'access_token': 'glpat-fresh',
        'refresh_token': 'refresh-2',
        'expires_in': 7200,
    }
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__.return_value = client
    with patch('server.auth.oauth_v2_refresh.httpx.AsyncClient', return_value=client):
        token = await get_oauth_v2_git_token(str(user_id), ProviderType.GITLAB)

    assert token == 'glpat-fresh'
    assert client.post.await_args.args[0] == 'https://gitlab.example.com/oauth/token'


async def _run_scheduled(schedule: Callable[[], None]) -> None:
    before = asyncio.all_tasks()
    schedule()
    await asyncio.gather(*(asyncio.all_tasks() - before))


@pytest.mark.asyncio
async def test_repo_sync_lists_projects_with_the_oauth_v2_token(
    oauth_v2_gitlab_user_id, oauth_v2_gitlab_token
):
    calls = []

    async def record(self, *args, **kwargs):
        token = await self.get_latest_token()
        calls.append((self.external_auth_id, token.get_secret_value(), args, kwargs))
        return []

    # Listing projects calls the GitLab API, which is what's faked.
    with patch.object(OAuthV2GitLabService, 'get_all_repositories', new=record):
        await _run_scheduled(
            lambda: schedule_oauth_v2_gitlab_repo_sync(oauth_v2_gitlab_user_id)
        )

    assert calls == [
        (
            oauth_v2_gitlab_user_id,
            oauth_v2_gitlab_token,
            ('pushed', AppMode.SAAS),
            {'store_in_background': False},
        )
    ]


@pytest.mark.asyncio
async def test_repo_sync_skips_a_user_without_a_token(gitlab_git_provider, create_user):
    list_repositories = AsyncMock()
    with patch.object(
        OAuthV2GitLabService, 'get_all_repositories', new=list_repositories
    ):
        await _run_scheduled(
            lambda: schedule_oauth_v2_gitlab_repo_sync(str(create_user().id))
        )

    list_repositories.assert_not_awaited()
