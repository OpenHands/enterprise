from pydantic import BaseModel, Field

from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.settings.settings_models import Settings

# OSS mode's single user (``DefaultUserAuth``). Records it creates are owned by
# this id, so per-user scoping applies to them like any other user's.
LOCAL_USER_ID = 'root'


class UserInfo(Settings):
    """Model for user settings including the current user id."""

    id: str | None = None


class ProviderTokenPage:
    items: list[PROVIDER_TOKEN_TYPE]
    next_page_id: str | None = None


class NamedOAuthSecret(BaseModel):
    """A conversation secret backed by the user's token for one OAuth provider."""

    secret_name: str
    oauth_provider_id: int
    description: str


class NamedOAuthSecrets(BaseModel):
    secrets: list[NamedOAuthSecret] = Field(default_factory=list)
    # Provider types of the git providers the user holds an OAuth v2 token
    # for. Their ``<PROVIDER_TYPE>_TOKEN`` provider secrets are replaced by
    # ``secrets``.
    linked_provider_types: set[ProviderType] = Field(default_factory=set)
