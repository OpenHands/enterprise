import hashlib
import hmac
import os
import time
from importlib import import_module
from urllib.parse import urlsplit

import jwt
import requests  # type: ignore
from fastapi import HTTPException

from openhands.app_server.config_api.client_config_types import (
    AuthCapabilities,
    ClientConfig,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.server_config.server_config import ServerConfig
from openhands.app_server.types import AppMode
from server.auth.auth_config import AUTH_MODE, ENABLE_KEYCLOAK, get_native_auth_settings
from server.auth.constants import (
    AZURE_DEVOPS_CLIENT_ID,
    BITBUCKET_APP_CLIENT_ID,
    BITBUCKET_DATA_CENTER_CLIENT_ID,
    ENABLE_AUTOMATIONS,
    ENABLE_ENTERPRISE_SSO,
    ENABLE_JIRA,
    ENABLE_JIRA_DC,
    ENABLE_LINEAR,
    GITHUB_APP_CLIENT_ID,
    GITHUB_APP_PRIVATE_KEY,
    GITHUB_APP_WEBHOOK_SECRET,
    GITLAB_APP_CLIENT_ID,
    RECAPTCHA_SITE_KEY,
)
from server.constants import DEPLOYMENT_MODE


def get_auth_capabilities(
    configured_providers: list[ProviderType] | None = None,
) -> AuthCapabilities:
    """One public login/connection contract for both configuration endpoints."""
    if not ENABLE_KEYCLOAK:
        from server.auth.saml_config import get_saml_settings

        saml = get_saml_settings()
        capabilities: AuthCapabilities = {
            'auth_mode': AUTH_MODE,
            'login_methods': ['password', 'saml'] if saml else ['password'],
            'git_connection_methods': {},
        }
        if saml:
            capabilities['saml'] = {
                'connection_id': saml.connection_id,
                'name': saml.name,
            }
        return capabilities
    providers = configured_providers
    if providers is None:
        providers = []
        for provider, configured in (
            (ProviderType.GITHUB, GITHUB_APP_CLIENT_ID),
            (ProviderType.GITLAB, GITLAB_APP_CLIENT_ID),
            (ProviderType.BITBUCKET, BITBUCKET_APP_CLIENT_ID),
            (ProviderType.ENTERPRISE_SSO, ENABLE_ENTERPRISE_SSO),
            (ProviderType.BITBUCKET_DATA_CENTER, BITBUCKET_DATA_CENTER_CLIENT_ID),
            (ProviderType.AZURE_DEVOPS, AZURE_DEVOPS_CLIENT_ID),
        ):
            if configured:
                providers.append(provider)
    return {
        'auth_mode': AUTH_MODE,
        'login_methods': sorted(provider.value for provider in providers),
        'git_connection_methods': {
            provider.value: ['oauth']
            for provider in providers
            if provider != ProviderType.ENTERPRISE_SSO
        },
    }


def get_native_cors_origins() -> list[str]:
    """Explicit trusted browser origins, including the configured app origin."""
    indexed = [
        (int(name.removeprefix('OH_PERMITTED_CORS_ORIGINS_')), value)
        for name, value in os.environ.items()
        if name.startswith('OH_PERMITTED_CORS_ORIGINS_')
        and name.removeprefix('OH_PERMITTED_CORS_ORIGINS_').isdigit()
    ]
    configured = (
        [value for _, value in sorted(indexed)]
        if indexed
        else os.getenv('PERMITTED_CORS_ORIGINS', '').split(',')
    )
    origins = [get_native_auth_settings().app_origin]
    for value in configured:
        origin = value.strip().rstrip('/')
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ('http', 'https')
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or '*' in origin
        ):
            raise ValueError('Native CORS requires explicit trusted HTTP(S) origins')
        parsed.port
        origins.append(origin)
    return list(dict.fromkeys(origins))


def validate_native_auth_configuration() -> None:
    """Reject incompatible selected adapters before constructing application services.

    Extensions may subclass the mode-aware SaaS adapters, retaining their
    security checks. Independent implementations must explicitly declare
    ``supports_native_auth = True`` and honor the same identity/session contract.
    """
    if ENABLE_KEYCLOAK:
        return
    get_native_auth_settings()
    get_native_cors_origins()
    get_auth_capabilities()
    from server.auth.ancillary_config import validate_native_ancillary_config

    validate_native_ancillary_config()
    for name in ('APP_MODE', 'OH_APP_MODE'):
        if os.getenv(name, 'saas').lower() != 'saas':
            raise ValueError(
                f'{name} must remain saas for native Enterprise authentication'
            )
    selectors = {
        'OPENHANDS_CONFIG_CLS': 'server.config.SaaSServerConfig',
        'OH_USER_KIND': 'openhands.app_server.user.auth_user_context.AuthUserContextInjector',
        'OH_LIFESPAN_KIND': 'server.app_lifespan.saas_app_lifespan_service.SaasAppLifespanService',
        'OH_WEB_CLIENT_KIND': 'openhands.app_server.web_client.default_web_client_config_injector.DefaultWebClientConfigInjector',
    }
    for name, default in selectors.items():
        selected = os.getenv(name)
        if not selected or selected == default:
            continue
        module, _, class_name = selected.rpartition('.')
        if not module:
            raise ValueError(f'{name} must select a mode-aware Enterprise adapter')
        try:
            cls = getattr(import_module(module), class_name)
        except (ImportError, AttributeError) as exc:
            raise ValueError(
                f'{name} selects an unavailable authentication adapter'
            ) from exc
        if not getattr(cls, 'supports_native_auth', False):
            raise ValueError(
                f'{name} selects an adapter incompatible with native authentication'
            )


def sign_token(payload: dict[str, object], jwt_secret: str, algorithm='HS256') -> str:
    """Signs a JWT token."""
    return jwt.encode(payload, jwt_secret, algorithm=algorithm)


def verify_signature(payload: bytes, signature: str):
    if not signature:
        raise HTTPException(
            status_code=403, detail='x-hub-signature-256 header is missing!'
        )

    expected_signature = (
        'sha256='
        + hmac.new(
            GITHUB_APP_WEBHOOK_SECRET.encode('utf-8'),
            msg=payload,
            digestmod=hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(status_code=403, detail="Request signatures didn't match!")


class SaaSServerConfig(ServerConfig):
    supports_native_auth = True
    config_cls: str = os.environ.get('OPENHANDS_CONFIG_CLS', '')
    app_mode: AppMode = AppMode.SAAS
    posthog_client_key: str = os.environ.get('POSTHOG_CLIENT_KEY', '')
    github_client_id: str = os.environ.get('GITHUB_APP_CLIENT_ID', '')
    enable_billing = os.environ.get('ENABLE_BILLING', 'false') == 'true'
    hide_llm_settings = os.environ.get('HIDE_LLM_SETTINGS', 'false') == 'true'
    auth_url: str | None = os.environ.get('AUTH_URL')
    settings_store_class: str = 'storage.saas_settings_store.SaasSettingsStore'
    secret_store_class: str = 'storage.saas_secrets_store.SaasSecretsStore'
    user_auth_class: str = 'server.auth.saas_user_auth.SaasUserAuth'
    conversation_secret_enricher_class: str | None = (
        'integrations.jira_dc.jira_dc_conversation_secret_enricher.'
        'JiraDcConversationSecretEnricher'
    )
    analytics_user_provider_class: str = (
        'analytics.saas_user_provider.SaasAnalyticsUserProvider'
    )
    # Maintenance window configuration
    maintenance_start_time: str = os.environ.get(
        'MAINTENANCE_START_TIME', ''
    )  # Timestamp in EST e.g 2025-07-29T14:18:01.219616-04:00
    enable_jira = ENABLE_JIRA
    enable_jira_dc = ENABLE_JIRA_DC
    enable_linear = ENABLE_LINEAR
    enable_automations = ENABLE_AUTOMATIONS
    enable_onboarding = os.environ.get('OH_ENABLE_ONBOARDING', 'false') == 'true'

    app_slug: None | str = None

    def __init__(self) -> None:
        if ENABLE_KEYCLOAK:
            self._get_app_slug()
        else:
            # Provider metadata must never make local authentication depend on
            # GitHub availability. Connection actions can resolve it lazily.
            self.app_slug = os.getenv('GITHUB_APP_SLUG') or None

    def _get_app_slug(self):
        """Retrieves the GitHub App slug using the GitHub API's /app endpoint by generating a JWT for the app.

        Raises:
            HTTPException: If the request to the GitHub API fails.
        """
        if not GITHUB_APP_CLIENT_ID or not GITHUB_APP_PRIVATE_KEY:
            return

        now = int(time.time())
        payload = {
            'iat': now - 60,  # Issued at time (backdate 60 seconds for clock skew)
            'exp': now
            + (
                9 * 60
            ),  # Expiration time (set to 9 minutes as 10 was causing error if there is time drift)
            'iss': GITHUB_APP_CLIENT_ID,  # GitHub App ID
        }

        encoded_jwt = sign_token(payload, GITHUB_APP_PRIVATE_KEY, algorithm='RS256')  # type: ignore

        headers = {
            'Authorization': f'Bearer {encoded_jwt}',
            'Accept': 'application/vnd.github+json',
        }

        response = requests.get('https://api.github.com/app', headers=headers)

        if response.status_code != 200:
            raise ValueError(
                f'Failed to retrieve app info, status code:{response.status_code}, message:{response.content.decode("utf-8")}'
            )

        app_data = response.json()
        self.app_slug = app_data.get('slug')

        if not self.app_slug:
            raise ValueError("GitHub app slug is missing in the API response.'")

    def verify_config(self) -> None:
        if not self.config_cls:
            raise ValueError('Config path not provided!')

        if ENABLE_KEYCLOAK and not self.posthog_client_key:
            raise ValueError('Missing posthog client key in env')

        if GITHUB_APP_CLIENT_ID and not self.github_client_id:
            raise ValueError('Missing Github client id')

    def get_config(self) -> ClientConfig:
        # These providers are configurable via helm charts for self hosted deployments
        # The FE should have this info so that the login buttons reflect the supported IDPs
        providers_configured = []
        if GITHUB_APP_CLIENT_ID:
            providers_configured.append(ProviderType.GITHUB)

        if GITLAB_APP_CLIENT_ID:
            providers_configured.append(ProviderType.GITLAB)

        if BITBUCKET_APP_CLIENT_ID:
            providers_configured.append(ProviderType.BITBUCKET)

        if ENABLE_ENTERPRISE_SSO:
            providers_configured.append(ProviderType.ENTERPRISE_SSO)

        if BITBUCKET_DATA_CENTER_CLIENT_ID:
            providers_configured.append(ProviderType.BITBUCKET_DATA_CENTER)

        if AZURE_DEVOPS_CLIENT_ID:
            providers_configured.append(ProviderType.AZURE_DEVOPS)

        config: ClientConfig = {
            **get_auth_capabilities(providers_configured),
            'APP_MODE': self.app_mode,
            'APP_SLUG': self.app_slug,
            'GITHUB_CLIENT_ID': self.github_client_id,
            'POSTHOG_CLIENT_KEY': self.posthog_client_key,
            'FEATURE_FLAGS': {
                'ENABLE_BILLING': self.enable_billing,
                'HIDE_LLM_SETTINGS': self.hide_llm_settings,
                'ENABLE_JIRA': self.enable_jira,
                'ENABLE_JIRA_DC': self.enable_jira_dc,
                'ENABLE_LINEAR': self.enable_linear,
                'ENABLE_AUTOMATIONS': self.enable_automations,
                'DEPLOYMENT_MODE': DEPLOYMENT_MODE,
                'ENABLE_ONBOARDING': self.enable_onboarding,
            },
            'PROVIDERS_CONFIGURED': providers_configured,
        }
        if not ENABLE_KEYCLOAK:
            config['PROVIDERS_CONFIGURED'] = []

        if self.maintenance_start_time:
            config['MAINTENANCE'] = {
                'startTime': self.maintenance_start_time,
            }

        if ENABLE_KEYCLOAK and self.auth_url:
            config['AUTH_URL'] = self.auth_url

        if RECAPTCHA_SITE_KEY:
            config['RECAPTCHA_SITE_KEY'] = RECAPTCHA_SITE_KEY

        return config
