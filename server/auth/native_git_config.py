"""Operator-owned Git provider endpoints; browser input never selects an API URL."""

import os
import re
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlsplit

from server.auth.native_session import get_app_origin
from server.auth.native_types import GitProviderCapability

CORE_HOSTS = {
    'github': 'github.com',
}


def normalize_host(value: str) -> str:
    parsed = urlsplit(value if '://' in value else f'https://{value}')
    host = parsed.hostname or ''
    if (
        parsed.scheme != 'https'
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in ('', '/')
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host)
        or '..' in host
    ):
        raise ValueError('Git provider host must be an HTTPS hostname')
    return host.lower()


@dataclass(frozen=True)
class NativeGitConfig:
    provider: str
    host: str
    methods: tuple[str, ...]

    @property
    def api_url(self) -> str:
        return (
            'https://api.github.com'
            if self.host == 'github.com'
            else f'https://{self.host}/api/v3'
        )

    @property
    def authorize_url(self) -> str:
        return f'https://{self.host}/login/oauth/authorize'

    @property
    def token_url(self) -> str:
        return f'https://{self.host}/login/oauth/access_token'

    @property
    def callback_url(self) -> str:
        return f'{get_app_origin()}/oauth/git/{self.provider}/callback'

    @property
    def client_id(self) -> str:
        return os.getenv(f'{self.provider.upper()}_APP_CLIENT_ID', '')

    @property
    def client_secret(self) -> str:
        return os.getenv(f'{self.provider.upper()}_APP_CLIENT_SECRET', '')


def native_git_capabilities() -> dict[str, GitProviderCapability]:
    from server.config import get_auth_capabilities

    methods = get_auth_capabilities()['git_connection_methods']
    result: dict[str, GitProviderCapability] = {}
    for provider, provider_methods in methods.items():
        if provider not in CORE_HOSTS:
            raise ValueError(f'Native Git connections are unsupported for {provider}')
        hosts = list(
            dict.fromkeys(
                normalize_host(value.strip())
                for value in os.getenv(
                    f'NATIVE_GIT_{provider.upper()}_HOSTS',
                    os.getenv(f'{provider.upper()}_HOST') or CORE_HOSTS[provider],
                ).split(',')
            )
        )
        result[provider] = {
            'methods': provider_methods,
            'hosts': hosts,
            'default_host': hosts[0],
        }
        if provider == 'github':
            result[provider]['installation_available'] = bool(
                (os.getenv('GITHUB_APP_ID') or os.getenv('GITHUB_APP_CLIENT_ID'))
                and os.getenv('GITHUB_APP_PRIVATE_KEY')
            )
    return result


def git_config(provider: str, host: str | None = None) -> NativeGitConfig:
    capabilities = native_git_capabilities()
    if provider not in capabilities:
        raise ValueError('Git provider is not enabled')
    capability = capabilities[provider]
    host = normalize_host(host) if host else capability['default_host']
    if host not in capability['hosts']:
        raise ValueError('Git provider host is not approved')
    return NativeGitConfig(provider, host, tuple(capability['methods']))


def validate_native_git_selectors() -> None:
    """Select SaaS token resolvers before the base app imports provider factories."""
    from importlib import import_module

    for provider, class_name in (('github', 'SaaSGitHubService'),):
        name = f'OPENHANDS_{provider.upper()}_SERVICE_CLS'
        default = f'integrations.{provider}.{provider}_service.{class_name}'
        selected = os.environ.setdefault(name, default)
        if selected == default:
            continue
        module, _, cls_name = selected.rpartition('.')
        try:
            cls = getattr(import_module(module), cls_name)
        except (ImportError, AttributeError, ValueError) as exc:
            raise ValueError(
                f'{name} selects an unavailable native Git adapter'
            ) from exc
        if not getattr(cls, 'supports_native_auth', False):
            raise ValueError(f'{name} must select a native-compatible SaaS Git adapter')
    for provider in (
        'GITLAB',
        'BITBUCKET',
        'BITBUCKET_DATA_CENTER',
        'AZURE_DEVOPS',
        'FORGEJO',
    ):
        # Stale optional selectors cannot expose unsupported integrations.
        for method in ('MANUAL', 'OAUTH'):
            if os.getenv(
                f'NATIVE_GIT_{provider}_{method}_ENABLED', 'false'
            ).lower() in ('true', '1'):
                raise ValueError(
                    f'Native Git connections are unsupported for {provider.lower()}'
                )


def github_app_issuer(client_id: str | None = None) -> str:
    from server.auth.auth_config import ENABLE_KEYCLOAK
    from server.auth.constants import GITHUB_APP_CLIENT_ID

    if client_id is None:
        client_id = GITHUB_APP_CLIENT_ID
    if ENABLE_KEYCLOAK:
        return client_id
    return os.getenv('GITHUB_APP_ID') or client_id


class GitHubApiOptions(TypedDict, total=False):
    base_url: str


def github_api_kwargs() -> GitHubApiOptions:
    from server.auth.auth_config import ENABLE_KEYCLOAK

    return {} if ENABLE_KEYCLOAK else {'base_url': git_config('github').api_url}
