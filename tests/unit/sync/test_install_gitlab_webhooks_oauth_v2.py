"""The worker's GitLab webhook installer reads ``oauth_tokens`` only.

Runs against the test database; any Keycloak call or Keycloak token read
fails the test.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from integrations.gitlab.gitlab_service import OAuthV2GitLabService
from server.auth.token_manager import TokenManager
from storage.gitlab_webhook import GitlabWebhook
from sync.install_gitlab_webhooks_oauth_v2 import OAuthV2VerifyWebhookStatus


def _fail_keycloak(*args, **kwargs):
    raise AssertionError('Keycloak must not be used')


@pytest.fixture(autouse=True)
def no_keycloak(async_session_maker):
    with (
        patch('storage.gitlab_webhook_store.a_session_maker', async_session_maker),
        patch('sync.install_gitlab_webhooks.a_session_maker', async_session_maker),
        patch('server.auth.token_manager.get_keycloak_admin', _fail_keycloak),
        patch('server.auth.token_manager.get_keycloak_openid', _fail_keycloak),
        patch.object(TokenManager, 'get_idp_token', _fail_keycloak),
        patch.object(TokenManager, 'load_offline_token', _fail_keycloak),
        patch.object(TokenManager, 'get_idp_token_from_offline_token', _fail_keycloak),
    ):
        yield


@pytest.mark.asyncio
async def test_a_user_with_a_token_gets_an_oauth_v2_service(
    oauth_v2_gitlab_user_id, oauth_v2_gitlab_token
):
    service = await OAuthV2VerifyWebhookStatus().gitlab_service_for(
        oauth_v2_gitlab_user_id
    )

    assert isinstance(service, OAuthV2GitLabService)
    latest = await service.get_latest_token()
    assert latest is not None
    assert latest.get_secret_value() == oauth_v2_gitlab_token


@pytest.mark.asyncio
async def test_a_user_without_a_token_is_skipped(gitlab_git_provider, create_user):
    user_id = str(create_user().id)
    assert await OAuthV2VerifyWebhookStatus().gitlab_service_for(user_id) is None


@pytest.mark.asyncio
async def test_a_run_checks_users_with_tokens_and_leaves_the_others_rows(
    oauth_v2_gitlab_user_id, oauth_v2_gitlab_token, create_user, async_session_maker
):
    user_without_token = str(create_user().id)
    async with async_session_maker() as session:
        session.add_all(
            [
                GitlabWebhook(
                    project_id='1',
                    user_id=oauth_v2_gitlab_user_id,
                    webhook_exists=False,
                ),
                GitlabWebhook(
                    project_id='2', user_id=user_without_token, webhook_exists=False
                ),
            ]
        )
        await session.commit()

    tokens: dict[str, str] = {}

    async def record(*, gitlab_service, resource_id, **kwargs):
        token = await gitlab_service.get_latest_token()
        tokens[resource_id] = token.get_secret_value()

    # The GitLab API calls are what's faked.
    with (
        patch.object(
            OAuthV2VerifyWebhookStatus,
            'verify_conditions_are_met',
            new=AsyncMock(side_effect=record),
        ),
        patch.object(
            OAuthV2VerifyWebhookStatus, 'create_new_webhook', new=AsyncMock()
        ) as create,
    ):
        await OAuthV2VerifyWebhookStatus().install_webhooks()

    assert tokens == {'1': oauth_v2_gitlab_token}
    create.assert_awaited_once()
    async with async_session_maker() as session:
        rows = {
            row.project_id: row
            for row in (await session.execute(select(GitlabWebhook))).scalars()
        }
    assert set(rows) == {'1', '2'}
    assert rows['2'].webhook_exists is False
    assert rows['2'].last_synced is not None
