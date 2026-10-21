from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
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
