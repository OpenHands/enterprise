"""Slack OAuth client config: an enabled ``auth_providers`` row, else env vars."""

from urllib.parse import parse_qs, urlparse

import pytest

from integrations.slack import slack_oauth_config
from integrations.slack.slack_oauth_config import (
    SLACK_BOT_SCOPES,
    SlackNotConfiguredError,
    get_slack_oauth_config,
    is_slack_configured,
)


@pytest.fixture
def env_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_oauth_config, 'SLACK_CLIENT_ID', 'env-client-id')
    monkeypatch.setattr(slack_oauth_config, 'SLACK_CLIENT_SECRET', 'env-secret')


@pytest.fixture
def no_env_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_oauth_config, 'SLACK_CLIENT_ID', None)
    monkeypatch.setattr(slack_oauth_config, 'SLACK_CLIENT_SECRET', None)


@pytest.mark.asyncio
async def test_enabled_provider_row_wins_over_env(env_client, create_slack_provider):
    await create_slack_provider(client_id='db-id', client_secret='db-secret')

    config = await get_slack_oauth_config()

    assert (config.client_id, config.client_secret) == ('db-id', 'db-secret')
    assert config.scopes == SLACK_BOT_SCOPES


@pytest.mark.asyncio
async def test_provider_scopes_override_defaults(no_env_client, create_slack_provider):
    await create_slack_provider(scopes=['chat:write'])

    config = await get_slack_oauth_config()

    assert config.scopes == ['chat:write']
    query = parse_qs(urlparse(config.authorize_url_generator().generate('s')).query)
    assert query['scope'] == ['chat:write']
    assert query['client_id'] == ['db-client-id']


@pytest.mark.asyncio
async def test_disabled_provider_row_falls_back_to_env(
    env_client, create_slack_provider
):
    await create_slack_provider(enabled=False)

    config = await get_slack_oauth_config()

    assert (config.client_id, config.client_secret) == ('env-client-id', 'env-secret')


@pytest.mark.asyncio
async def test_env_only(env_client, oauth_v2_stores):
    config = await get_slack_oauth_config()

    assert (config.client_id, config.client_secret) == ('env-client-id', 'env-secret')
    assert config.scopes == SLACK_BOT_SCOPES


@pytest.mark.asyncio
async def test_not_configured_raises(no_env_client, oauth_v2_stores):
    with pytest.raises(SlackNotConfiguredError):
        await get_slack_oauth_config()


@pytest.mark.asyncio
async def test_user_scopes_are_added_to_authorize_url(
    no_env_client, create_slack_provider
):
    await create_slack_provider()

    config = await get_slack_oauth_config()

    url = config.authorize_url_generator(user_scopes=['search:read']).generate('s')
    assert parse_qs(urlparse(url).query)['user_scope'] == ['search:read']


@pytest.fixture
def slack_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('SLACK_WEBHOOKS_ENABLED', '1')
    monkeypatch.setenv('SLACK_SIGNING_SECRET', 'signing')


@pytest.mark.asyncio
async def test_is_slack_configured_with_provider_row(
    slack_deployment, no_env_client, create_slack_provider
):
    await create_slack_provider()

    assert await is_slack_configured()


@pytest.mark.asyncio
async def test_is_slack_configured_requires_client_secret(
    slack_deployment, no_env_client, create_slack_provider
):
    await create_slack_provider(client_secret=None)

    assert not await is_slack_configured()


@pytest.mark.asyncio
async def test_is_slack_configured_requires_webhooks_enabled(
    monkeypatch, no_env_client, create_slack_provider
):
    monkeypatch.delenv('SLACK_WEBHOOKS_ENABLED', raising=False)
    monkeypatch.setenv('SLACK_SIGNING_SECRET', 'signing')
    await create_slack_provider()

    assert not await is_slack_configured()


@pytest.mark.asyncio
async def test_is_slack_configured_false_without_any_client(
    slack_deployment, no_env_client, oauth_v2_stores
):
    assert not await is_slack_configured()
