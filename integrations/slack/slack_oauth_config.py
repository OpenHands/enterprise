"""Slack app OAuth client configuration.

An enabled ``auth_providers`` row with ``provider_type='slack'`` (managed under
``/super-admin/auth-providers``) wins; otherwise the ``SLACK_CLIENT_ID`` /
``SLACK_CLIENT_SECRET`` environment variables are used. The signing secret and
``SLACK_WEBHOOKS_ENABLED`` stay in the environment: they are not OAuth client
settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from slack_sdk.oauth import AuthorizeUrlGenerator

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.slack_config import is_slack_deployment_enabled
from server.constants import SLACK_CLIENT_ID, SLACK_CLIENT_SECRET
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_store import AuthProviderStore

SLACK_BOT_SCOPES = [
    'app_mentions:read',
    'chat:write',
    'users:read',
    'files:read',
    'channels:history',
    'groups:history',
    'mpim:history',
    'im:history',
]
SLACK_USER_SCOPES = ['search:read']


class SlackNotConfiguredError(Exception):
    """Neither an enabled Slack auth provider nor the env vars configure Slack."""


@dataclass(frozen=True)
class SlackOAuthConfig:
    client_id: str
    client_secret: str | None
    scopes: list[str]

    def authorize_url_generator(
        self, user_scopes: list[str] | None = None
    ) -> AuthorizeUrlGenerator:
        return AuthorizeUrlGenerator(
            client_id=self.client_id, scopes=self.scopes, user_scopes=user_scopes
        )


async def get_slack_oauth_config() -> SlackOAuthConfig:
    provider = await AuthProviderStore().get_provider_by_type(
        ProviderType.SLACK.value, enabled_only=True
    )
    if provider is not None and isinstance(provider.details, OAuthProviderDetails):
        details = provider.details
        return SlackOAuthConfig(
            client_id=details.client_id,
            client_secret=details.client_secret.get_secret_value()
            if details.client_secret
            else None,
            scopes=details.scopes or SLACK_BOT_SCOPES,
        )
    if SLACK_CLIENT_ID:
        return SlackOAuthConfig(
            client_id=SLACK_CLIENT_ID,
            client_secret=SLACK_CLIENT_SECRET,
            scopes=SLACK_BOT_SCOPES,
        )
    raise SlackNotConfiguredError('Slack OAuth client is not configured')


async def is_slack_configured() -> bool:
    """Whether Slack is usable: webhooks on, signing secret set, OAuth client set.

    The client may come from an enabled Slack auth provider or the env vars, so
    unlike ``openhands.app_server.utils.slack_config.is_slack_configured`` this
    needs the database.
    """
    if not is_slack_deployment_enabled():
        return False
    try:
        config = await get_slack_oauth_config()
    except SlackNotConfiguredError:
        return False
    return bool(config.client_id and config.client_secret)
