from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from openhands.agent_server.env_parser import DiscriminatedUnionMixin
from openhands.app_server.config_api.config_models import AppMode
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.web_client.web_client_deployment_mode import (
    DeploymentMode,
    get_deployment_mode,
)


class WebClientFeatureFlags(BaseModel):
    enable_billing: bool = False
    hide_llm_settings: bool = False
    enable_jira: bool = False
    enable_jira_dc: bool = False
    enable_linear: bool = False
    hide_users_page: bool = False
    hide_billing_page: bool = False
    hide_integrations_page: bool = False
    # When true, the web client hides personal workspaces from the org list
    # and selector for users who belong to at least one team org. Used by
    # OHE installs that bootstrap a default org and want it to be the only
    # workspace users see. UI-level only — the orgs API still returns
    # personal orgs, and disabling the flag restores them.
    hide_personal_workspaces: bool = False
    # When false, the web client hides the BYOK editing UI (custom model
    # string, base URL, and API key inputs) in LLM settings, leaving only the
    # managed model dropdown. Used by OHE installs where admins curate the
    # model list on the bundled LiteLLM proxy. Defaults to True so SaaS and
    # existing installs are unaffected. UI-level only — previously saved BYOK
    # settings keep working at runtime.
    allow_user_llm_configuration: bool = True
    # Defaults to True so the ACP agent configuration UI (Settings > Agent) is
    # visible on SaaS and existing installs, matching Agent Canvas. Set
    # ENABLE_ACP=false to hide it. UI-level only.
    enable_acp: bool = True
    deployment_mode: DeploymentMode | None = None
    enable_onboarding: bool = False
    enable_automations: bool = True
    enable_agent_canvas_banner: bool = False
    # When true, LLM API key export (BYOR) is enabled for every org regardless of
    # billing/credits. Mirrors the ENABLE_BYOR_EXPORT env var so the frontend can
    # distinguish "export disabled on this deployment" from "buy credits to enable".
    enable_byor_export: bool = False
    # When false, the web client hides Super Admin entry points and redirects
    # /super-admin/* to Settings. Defaults to False so the unfinished
    # Super Admin dashboard stays off in production. To turn it on, set both
    # ENABLE_SUPER_ADMIN=true (or 1), which the server reads to mount the
    # /api/admin directory, and OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_SUPER_ADMIN=true:
    # once any OH_WEB_CLIENT_FEATURE_FLAGS_* is set, flags are built from those
    # alone and the bare variable no longer reaches this field.
    enable_super_admin: bool = False
    # Whether the integrated, locally-hosted IDP (email+password login) is
    # available -- mirrors whether its oauth_providers row exists (see
    # server.routes.idp.is_idp_available). The frontend uses this to show an
    # email+password login form instead of OAuth provider buttons.
    enable_integrated_idp: bool = False
    # Mirrors the ENABLE_OAUTH_V2_LOGIN env var. When true, the frontend
    # sends unauthenticated users to the new OAuth v2 entry point
    # (/oauth/idp-login, a backend redirect) instead of the legacy Keycloak
    # /login page. Defaults to False so existing installs keep the legacy
    # login path until they opt in.
    enable_oauth_v2_login: bool = False
    # Deployment-wide switch for the bundled/external LiteLLM gateway.
    # Defaults to True for backward compatibility with existing installs.
    # When False, the frontend must hide every feature that depends on
    # LiteLLM (Budgets, managed/OpenHands models, managed LLM key
    # create/refresh) and show a "Please enable LiteLLM to use this
    # feature" placeholder instead. Set from the ENABLE_LEGACY_LITELLM env var.
    enable_litellm: bool = True
    # OHE-3456 settings pages, hidden until bug bash is complete. Each gates
    # one Personal Settings entry: ``enable_agent_context_page`` shows the
    # dedicated Agent Context page (``/settings/agent-context``) and
    # ``enable_agent_profiles_page`` replaces ``Agent`` with Agent Profiles
    # (``/settings/agents``) and, because that Agent page hosted the memory
    # toggle, also shows the Agent Context page. Set from ENABLE_AGENT_CONTEXT_PAGE and
    # ENABLE_AGENT_PROFILES_PAGE (or the OH_WEB_CLIENT_FEATURE_FLAGS_* form).
    # Default False so every existing install keeps today's settings UI.
    # UI-level only -- the agent-profiles API is not gated.
    enable_agent_context_page: bool = False
    enable_agent_profiles_page: bool = False
    # ``enable_org_agent_profiles`` lets org admins add, edit and delete the
    # organization's shared Agent Profiles on that page (OHE-3088). Set from
    # ENABLE_ORG_AGENT_PROFILES. UI-level only -- the ``scope=org`` API and
    # its org-scope constraints are not gated.
    enable_org_agent_profiles: bool = False
    # Org Model Router (OHE-3508): when true, the settings shell shows the
    # org "Model Router" page and the server serves
    # /api/organizations/{org_id}/meta-profiles and applies the active router
    # to conversation launches. Set from ENABLE_MODEL_ROUTER (or the
    # OH_WEB_CLIENT_FEATURE_FLAGS_* form, which the server reads too). Default
    # False so existing installs keep today's behavior.
    enable_model_router: bool = False
    # Service accounts (OHE-3795): when false, the web client hides the
    # Service Accounts settings page and the server answers 403 on
    # /api/organizations/{org_id}/service-accounts*. A database feature flag
    # (ENABLE_SERVICE_ACCOUNTS) that falls back to the env var of the same
    # name, so a Super Admin can toggle it without a redeploy.
    enable_service_accounts: bool = False

    # This can be removed / replaced when a DeploymentMode (or similar) env var is created.
    @model_validator(mode='after')
    def set_deployment_mode(self) -> 'WebClientFeatureFlags':
        if self.deployment_mode is None:
            self.deployment_mode = get_deployment_mode()
        return self


class ACPProviderConfig(BaseModel):
    """A built-in ACP harness this deployment offers.

    Carries no model list: which models an ACP agent offers depends on the
    account it runs under, so the agent reports them once a session starts
    (``AppConversation.acp_available_models``).
    """

    key: str
    display_name: str
    default_command: list[str]
    api_key_env_var: str | None = None
    base_url_env_var: str | None = None


class WebClientConfig(DiscriminatedUnionMixin):
    app_mode: AppMode
    posthog_client_key: str | None
    feature_flags: WebClientFeatureFlags
    # Database-backed feature flags that are safe to expose to anonymous
    # callers: only flags with NO targeting rules (global on/off) ever land
    # here. Per-user/per-org/per-email flags require an authenticated context
    # and are fetched via a separate path. Defaults to empty so OSS and
    # pre-migration installs are unaffected.
    db_feature_flags: dict[str, bool] = Field(default_factory=dict)
    providers_configured: list[ProviderType]
    maintenance_start_time: datetime | None
    auth_url: str | None
    recaptcha_site_key: str | None
    faulty_models: list[str]
    error_message: str | None
    updated_at: datetime
    github_app_slug: str | None
    gitlab_enabled: bool = False
    provider_default_hosts: dict[str, str] = Field(default_factory=dict)
    slack_enabled: bool = False
    email_enabled: bool = False
    email_change_enabled: bool = True
    # True when USER_PROVISIONING_ENABLED registers the admin provision-user
    # route; the Super Admin Users page hides "Provision User" otherwise.
    user_provisioning_enabled: bool = False
    # Mirrors REQUIRE_VERIFIED_EMAILS: only then does the server block
    # unverified users, so the UI should only restrict them in that case.
    require_verified_emails: bool = False
    acp_providers: list[ACPProviderConfig] = Field(default_factory=list)
    # Hostname of the Jira Data Center server when DC OAuth is configured, so the
    # configure form can pre-fill and lock the host field (the OAuth callback only
    # accepts this exact host). None in email-match mode / when DC isn't configured.
    jira_dc_oauth_host: str | None = None
    # Optional OpenHands Enterprise/KOTS-managed Jira DC service account. When
    # configured, the frontend hides the in-app service-account secret entry and
    # the backend always prefers the env credentials at runtime.
    jira_dc_service_account_managed: bool = False
    jira_dc_service_account_email: str | None = None
    jira_dc_service_account_config_error: str | None = None
    # False when the install links Jira Cloud users by email match instead of
    # Atlassian OAuth; drives direct-save + manual-webhook UI in the configure
    # flow instead of the OAuth redirect.
    jira_oauth_enabled: bool = True
