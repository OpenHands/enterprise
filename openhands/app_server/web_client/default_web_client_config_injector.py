import os
from datetime import datetime
from urllib.parse import urlparse

from pydantic import Field

from openhands.app_server.integrations.jira_dc.config import (
    get_jira_dc_service_account_env_config,
)
from openhands.app_server.integrations.provider import ProviderHandler
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.web_client.email_change_config import (
    is_email_change_enabled,
)
from openhands.app_server.web_client.web_client_config_injector import (
    WebClientConfigInjector,
)
from openhands.app_server.web_client.web_client_models import (
    ACPModelOption,
    ACPProviderConfig,
    WebClientConfig,
    WebClientFeatureFlags,
)
from openhands.sdk.settings import ACP_PROVIDERS


def _env_flag_enabled(name: str, default: str = 'false') -> bool:
    return os.getenv(name, default).lower() in ('true', '1')


def _get_recaptcha_site_key() -> str | None:
    """Get reCAPTCHA site key from environment variable."""
    key = os.getenv('RECAPTCHA_SITE_KEY', '').strip()
    return key if key else None


# OSS default PostHog key - used when no environment variable is configured
_OSS_POSTHOG_KEY = 'phc_3ESMmY9SgqEAGBB6sMGK5ayYHkeUuknH2vP6FmWH9RA'


def _get_posthog_client_key() -> str:
    """Get PostHog client key from environment variable.

    Reads POSTHOG_CLIENT_KEY from environment. If not set or empty,
    returns the OSS default key for backwards compatibility.
    """
    key = os.getenv('POSTHOG_CLIENT_KEY', '').strip()
    return key if key else _OSS_POSTHOG_KEY


def _get_auth_url() -> str | None:
    """Get authentication service URL from environment variable.

    Reads AUTH_URL from environment. If not set or empty, returns None.
    """
    url = os.getenv('AUTH_URL', '').strip()
    return url if url else None


def _get_maintenance_start_time() -> datetime | None:
    """Get maintenance start time from environment variable.

    Reads MAINTENANCE_START_TIME from environment. If set to a valid ISO 8601
    timestamp, returns the parsed datetime. If empty, unset, or invalid,
    returns None (graceful fallback).
    """
    value = os.getenv('MAINTENANCE_START_TIME', '').strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_gitlab_enabled() -> bool:
    """Return whether GitLab OAuth is configured for the web client."""
    return bool(os.getenv('GITLAB_APP_CLIENT_ID', '').strip())


def _get_providers_configured() -> list[ProviderType]:
    """Get configured OAuth providers from environment variables.

    Checks for presence of OAuth client ID env vars and returns a list of
    configured providers. Mirrors legacy logic from SaaSServerConfig.
    """
    providers: list[ProviderType] = []

    if os.getenv('GITHUB_APP_CLIENT_ID', '').strip():
        providers.append(ProviderType.GITHUB)

    if _is_gitlab_enabled():
        providers.append(ProviderType.GITLAB)

    if os.getenv('BITBUCKET_APP_CLIENT_ID', '').strip():
        providers.append(ProviderType.BITBUCKET)

    if os.getenv('BITBUCKET_DATA_CENTER_CLIENT_ID', '').strip():
        providers.append(ProviderType.BITBUCKET_DATA_CENTER)

    if os.getenv('AZURE_DEVOPS_CLIENT_ID', '').strip():
        providers.append(ProviderType.AZURE_DEVOPS)

    if os.getenv('ENABLE_ENTERPRISE_SSO', '').strip():
        providers.append(ProviderType.ENTERPRISE_SSO)

    return providers


def _get_github_app_slug() -> str | None:
    """Get GitHub app slug from environment variable.

    Reads GITHUB_APP_SLUG from environment. If set, returns the value.
    If empty or unset, returns None.
    """
    slug = os.getenv('GITHUB_APP_SLUG', '').strip()
    return slug if slug else None


def _get_slack_enabled() -> bool:
    """Return whether Slack integration is fully configured for the web client."""
    return (
        os.getenv('SLACK_WEBHOOKS_ENABLED', 'false').lower() in ('true', '1')
        and bool(os.getenv('SLACK_CLIENT_ID', '').strip())
        and bool(os.getenv('SLACK_CLIENT_SECRET', '').strip())
        and bool(os.getenv('SLACK_SIGNING_SECRET', '').strip())
    )


def _get_email_enabled() -> bool:
    """Return whether transactional email delivery is configured."""
    try:
        from server.services.smtp_email_service import SMTPEmailService
    except Exception:
        smtp_enabled = bool(os.getenv('SMTP_HOST', '').strip())
    else:
        smtp_enabled = SMTPEmailService.is_configured()

    try:
        from server.services.email_service import EmailService
    except Exception:
        resend_enabled = bool(os.getenv('RESEND_API_KEY', '').strip())
    else:
        resend_enabled = EmailService.is_configured()

    return smtp_enabled or resend_enabled


def _get_jira_dc_oauth_host() -> str | None:
    """Hostname of the Jira Data Center server when DC OAuth is configured.

    Surfaced to the web client so the configure form can pre-fill and lock the
    workspace/host field in OAuth mode — the OAuth callback only accepts this
    exact host, so re-typing it is redundant and error-prone. Returns None in
    email-match mode (``JIRA_DC_ENABLE_OAUTH`` off) or when no base URL is set,
    leaving the host field free-text for the admin to enter per workspace.
    """
    if os.getenv('JIRA_DC_ENABLE_OAUTH', '1') not in ('1', 'true'):
        return None
    base_url = os.getenv('JIRA_DC_BASE_URL', '').strip()
    if not base_url:
        return None
    return urlparse(base_url).hostname or None


def _get_jira_oauth_enabled() -> bool:
    """Whether Jira Cloud links users via Atlassian OAuth.

    False in email-match mode (``JIRA_ENABLE_OAUTH`` off), where the configure
    flow saves the workspace directly and users are matched by email.
    """
    return os.getenv('JIRA_ENABLE_OAUTH', '1') in ('1', 'true')


def _get_jira_dc_service_account_config_error() -> str | None:
    """Return a web-client-safe service-account config error, if any."""
    return get_jira_dc_service_account_env_config().error


def _is_jira_dc_service_account_managed() -> bool:
    """Return whether Jira DC service-account credentials are env-managed."""
    return get_jira_dc_service_account_env_config().is_managed


def _get_jira_dc_service_account_email() -> str | None:
    """Return the env-managed service-account email when fully configured."""
    config = get_jira_dc_service_account_env_config()
    if not config.is_managed:
        return None
    return config.email


def _get_feature_flags() -> WebClientFeatureFlags:
    """Get feature flags from environment variables.

    Reads ENABLE_BILLING, HIDE_LLM_SETTINGS, ENABLE_JIRA, ENABLE_JIRA_DC,
    ENABLE_LINEAR, HIDE_USERS_PAGE, HIDE_BILLING_PAGE, HIDE_INTEGRATIONS_PAGE,
    HIDE_PERSONAL_WORKSPACES, OH_ENABLE_ONBOARDING, ENABLE_AGENT_CANVAS_BANNER,
    and ENABLE_BYOR_EXPORT from environment.

    OH_ALLOW_USER_LLM_CONFIGURATION and ENABLE_ACP are the exceptions: they
    default to 'true' when unset. OH_ALLOW_USER_LLM_CONFIGURATION keeps the
    BYOK editing UI visible; ENABLE_ACP keeps the ACP agent configuration UI
    (Settings > Agent) visible on SaaS and existing installs, matching Agent
    Canvas. Set ENABLE_ACP=false to hide it.

    enable_billing here is only the env-var fallback: ``get_web_client_config``
    re-resolves it against the DB-backed ENABLE_BILLING feature flag on every
    request (see ``_resolve_billing_enabled``).
    """
    return WebClientFeatureFlags(
        enable_billing=os.getenv('ENABLE_BILLING', 'false') == 'true',
        hide_llm_settings=os.getenv('HIDE_LLM_SETTINGS', 'false') == 'true',
        enable_jira=os.getenv('ENABLE_JIRA', 'false') == 'true',
        enable_jira_dc=os.getenv('ENABLE_JIRA_DC', 'false') == 'true',
        enable_linear=os.getenv('ENABLE_LINEAR', 'false') == 'true',
        hide_users_page=os.getenv('HIDE_USERS_PAGE', 'false') == 'true',
        hide_billing_page=os.getenv('HIDE_BILLING_PAGE', 'false') == 'true',
        hide_integrations_page=os.getenv('HIDE_INTEGRATIONS_PAGE', 'false') == 'true',
        hide_personal_workspaces=os.getenv('HIDE_PERSONAL_WORKSPACES', 'false')
        == 'true',
        allow_user_llm_configuration=os.getenv(
            'OH_ALLOW_USER_LLM_CONFIGURATION', 'true'
        )
        == 'true',
        enable_acp=os.getenv('ENABLE_ACP', 'true') == 'true',
        enable_onboarding=os.getenv('OH_ENABLE_ONBOARDING', 'false') == 'true',
        enable_automations=os.getenv('ENABLE_AUTOMATIONS', 'true') == 'true',
        enable_agent_canvas_banner=_env_flag_enabled('ENABLE_AGENT_CANVAS_BANNER'),
        enable_byor_export=_env_flag_enabled('ENABLE_BYOR_EXPORT'),
    )


async def _get_db_feature_flags() -> dict[str, bool]:
    """Return database-backed global feature flags for an anonymous context.

    Only flags with NO targeting rules are returned (see
    ``FeatureFlagService.get_global_flags``); per-user/per-org/per-email flags
    require an authenticated context and are intentionally absent here. This
    endpoint is one of the first invoked and does not require authentication,
    so it must not leak targeted flag state.

    The import is lazy and best-effort: OSS installs (and SaaS installs that
    have not applied the feature-flag migration) simply get an empty map, so
    this path degrades gracefully and never blocks the config endpoint.
    """
    try:
        from server.services.feature_flag_service import (
            feature_flag_service,
        )
    except Exception:
        return {}
    try:
        return await feature_flag_service.get_global_flags()
    except Exception:
        # Never let a DB hiccup take down the unauthenticated config
        # endpoint; fall back to an empty map.
        return {}


async def _resolve_billing_enabled(env_default: bool) -> bool:
    """Resolve the unified ENABLE_BILLING flag: database first, env fallback.

    ``ENABLE_BILLING`` is a DB-managed feature flag whose env var is the
    pre-migration fallback (see ``FeatureFlagService`` env-default pattern):
    when a ``FeatureFlag`` row exists it is authoritative; otherwise the
    ``ENABLE_BILLING`` env var decides. Like ``_get_db_feature_flags``, this
    is best-effort -- OSS installs and DB hiccups fall back to the env value
    so the unauthenticated config endpoint never breaks.
    """
    try:
        from server.services.feature_flag_service import (
            feature_flag_service,
        )
    except Exception:
        return env_default
    try:
        return await feature_flag_service.is_enabled('ENABLE_BILLING')
    except Exception:
        return env_default


class DefaultWebClientConfigInjector(WebClientConfigInjector):
    posthog_client_key: str = Field(default_factory=_get_posthog_client_key)
    feature_flags: WebClientFeatureFlags = Field(default_factory=_get_feature_flags)
    providers_configured: list[ProviderType] = Field(
        default_factory=_get_providers_configured
    )
    maintenance_start_time: datetime | None = Field(
        default_factory=_get_maintenance_start_time
    )
    auth_url: str | None = Field(default_factory=_get_auth_url)
    recaptcha_site_key: str | None = Field(default_factory=_get_recaptcha_site_key)
    faulty_models: list[str] = Field(default_factory=list)
    error_message: str | None = None
    updated_at: datetime = Field(
        default=datetime.fromisoformat('2026-01-01T00:00:00Z'),
        description=(
            'The timestamp when error messages and faulty models were last updated. '
            'The frontend uses this value to determine whether error messages are '
            'new and should be displayed. (Default to start of 2026)'
        ),
    )
    github_app_slug: str | None = Field(default_factory=_get_github_app_slug)
    gitlab_enabled: bool = Field(default_factory=_is_gitlab_enabled)
    provider_default_hosts: dict[str, str] = Field(
        default_factory=lambda: {
            provider.value: host
            for provider, host in ProviderHandler.PROVIDER_DOMAINS.items()
        }
    )
    slack_enabled: bool = Field(default_factory=_get_slack_enabled)
    email_enabled: bool = Field(default_factory=_get_email_enabled)
    email_change_enabled: bool = Field(default_factory=is_email_change_enabled)
    jira_dc_oauth_host: str | None = Field(default_factory=_get_jira_dc_oauth_host)
    jira_dc_service_account_managed: bool = Field(
        default_factory=_is_jira_dc_service_account_managed
    )
    jira_dc_service_account_email: str | None = Field(
        default_factory=_get_jira_dc_service_account_email
    )
    jira_dc_service_account_config_error: str | None = Field(
        default_factory=_get_jira_dc_service_account_config_error
    )
    jira_oauth_enabled: bool = Field(default_factory=_get_jira_oauth_enabled)
    acp_providers: list[ACPProviderConfig] = Field(
        default_factory=lambda: [
            ACPProviderConfig(
                key=provider.key,
                display_name=provider.display_name,
                default_command=list(provider.default_command),
                default_model=provider.default_model or None,
                available_models=[
                    ACPModelOption(id=m.id, label=m.label)
                    for m in (provider.available_models or [])
                ],
                api_key_env_var=provider.api_key_env_var,
                base_url_env_var=provider.base_url_env_var,
            )
            for provider in ACP_PROVIDERS.values()
        ]
    )

    async def get_web_client_config(self) -> WebClientConfig:
        from openhands.app_server.config import get_global_config

        config = get_global_config()
        # enable_billing is unified with the DB-backed ENABLE_BILLING feature
        # flag: a database row wins, the env var (baked into self.feature_flags
        # at init) is the fallback.
        feature_flags = self.feature_flags.model_copy(
            update={
                'enable_billing': await _resolve_billing_enabled(
                    self.feature_flags.enable_billing
                )
            }
        )
        result = WebClientConfig(
            app_mode=config.app_mode,
            posthog_client_key=self.posthog_client_key,
            feature_flags=feature_flags,
            db_feature_flags=await _get_db_feature_flags(),
            providers_configured=self.providers_configured,
            maintenance_start_time=self.maintenance_start_time,
            auth_url=self.auth_url,
            recaptcha_site_key=self.recaptcha_site_key,
            faulty_models=self.faulty_models,
            error_message=self.error_message,
            updated_at=self.updated_at,
            github_app_slug=self.github_app_slug,
            gitlab_enabled=self.gitlab_enabled,
            provider_default_hosts=self.provider_default_hosts,
            slack_enabled=self.slack_enabled,
            email_enabled=self.email_enabled,
            email_change_enabled=self.email_change_enabled,
            jira_dc_oauth_host=self.jira_dc_oauth_host,
            jira_dc_service_account_managed=self.jira_dc_service_account_managed,
            jira_dc_service_account_email=self.jira_dc_service_account_email,
            jira_dc_service_account_config_error=(
                self.jira_dc_service_account_config_error
            ),
            jira_oauth_enabled=self.jira_oauth_enabled,
            acp_providers=self.acp_providers,
        )
        return result
