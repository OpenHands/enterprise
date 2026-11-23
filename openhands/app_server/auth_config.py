import os

from pydantic import BaseModel, Field

from openhands.agent_server.env_parser import from_env


def _legacy_enable_oauth_v2_login() -> bool:
    """Value used while ``OH_AUTH_ENABLE_OAUTH_V2_LOGIN`` is unset.

    Both older spellings keep working. The structured web client form wins over
    the bare one, as it did for the web client.
    """
    value = os.getenv('OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_OAUTH_V2_LOGIN')
    if value is None:
        value = os.getenv('ENABLE_OAUTH_V2_LOGIN', 'false')
    return value.lower() in ('true', '1')


class AuthConfig(BaseModel):
    enable_oauth_v2_login: bool = Field(
        default_factory=_legacy_enable_oauth_v2_login,
        description=(
            'Use OAuth v2 login (OH_AUTH_ENABLE_OAUTH_V2_LOGIN). The backend and '
            'the web client both read this value, so they cannot disagree. The '
            'legacy ENABLE_OAUTH_V2_LOGIN and '
            'OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_OAUTH_V2_LOGIN variables apply '
            'while it is unset.'
        ),
    )


def get_auth_config() -> AuthConfig:
    """Resolve the auth config from the environment on every call.

    Unlike ``get_global_config()``, which builds every injector and is cached
    for the life of the process, this is cheap and sees environment changes.
    """
    return from_env(AuthConfig, 'OH_AUTH')  # type: ignore[return-value]
