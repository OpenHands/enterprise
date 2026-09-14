"""Public client configuration shared by the app and Enterprise servers."""

from typing import NotRequired, TypedDict

from openhands.app_server.config_api.config_models import AppMode
from openhands.app_server.integrations.service_types import ProviderType


class SamlCapability(TypedDict):
    connection_id: str
    name: str


class AuthCapabilities(TypedDict):
    auth_mode: str
    login_methods: list[str]
    saml: NotRequired[SamlCapability]
    git_connection_methods: dict[str, list[str]]


class ClientFeatureFlags(TypedDict):
    ENABLE_BILLING: bool
    HIDE_LLM_SETTINGS: bool
    ENABLE_JIRA: NotRequired[bool]
    ENABLE_JIRA_DC: NotRequired[bool]
    ENABLE_LINEAR: NotRequired[bool]
    ENABLE_AUTOMATIONS: NotRequired[bool]
    DEPLOYMENT_MODE: NotRequired[str]
    ENABLE_ONBOARDING: NotRequired[bool]


class ClientConfig(TypedDict):
    APP_MODE: AppMode
    GITHUB_CLIENT_ID: str
    POSTHOG_CLIENT_KEY: str
    FEATURE_FLAGS: ClientFeatureFlags
    APP_SLUG: NotRequired[str | None]
    PROVIDERS_CONFIGURED: NotRequired[list[ProviderType]]
    MAINTENANCE: NotRequired[dict[str, str]]
    AUTH_URL: NotRequired[str]
    RECAPTCHA_SITE_KEY: NotRequired[str]
    auth_mode: NotRequired[str]
    login_methods: NotRequired[list[str]]
    saml: NotRequired[SamlCapability]
    git_connection_methods: NotRequired[dict[str, list[str]]]
