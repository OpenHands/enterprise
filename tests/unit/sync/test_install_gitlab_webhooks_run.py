"""A webhook installer run, against the test database, without Keycloak and
with ``ENABLE_OAUTH_V2_LOGIN`` on."""

from unittest.mock import AsyncMock, patch

import pytest

from integrations.gitlab.gitlab_service import SaaSGitLabService
from openhands.app_server.integrations.service_types import ProviderType
from storage.gitlab_webhook import GitlabWebhook
from sync.install_gitlab_webhooks import VerifyWebhookStatus

pytestmark = pytest.mark.usefixtures('oauth_v2_login_on')


@pytest.mark.asyncio
async def test_a_run_uses_the_users_oauth_v2_gitlab_token(
    no_keycloak,
    create_git_provider,
    store_oauth_token,
    create_user,
    async_session_maker,
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user_id = create_user().id
    await store_oauth_token(user_id, gitlab, 'glpat-linked')
    async with async_session_maker() as session:
        session.add(
            GitlabWebhook(project_id='1', user_id=str(user_id), webhook_exists=False)
        )
        await session.commit()
    tokens: dict[str, str] = {}

    async def record(*, gitlab_service, resource_id, **kwargs):
        token = await gitlab_service.get_latest_token()
        tokens[resource_id] = token.get_secret_value()

    with (
        patch('storage.gitlab_webhook_store.a_session_maker', async_session_maker),
        patch('sync.install_gitlab_webhooks.a_session_maker', async_session_maker),
        # Deployments set OPENHANDS_GITLAB_SERVICE_CLS to SaaSGitLabService.
        patch('sync.install_gitlab_webhooks.GitLabServiceImpl', SaaSGitLabService),
        patch(
            'sync.install_gitlab_webhooks.install_claimed_webhook',
            new=AsyncMock(side_effect=record),
        ),
    ):
        await VerifyWebhookStatus().install_webhooks()

    assert tokens == {'1': 'glpat-linked'}
