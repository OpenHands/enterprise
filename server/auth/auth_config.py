"""Installation authentication mode. This module must not import auth clients."""

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from openhands.app_server.utils.web_url import get_web_url_from_env
from server.auth.config_env import get_config_env


def parse_enable_keycloak(value: str | None, *, name: str = 'ENABLE_KEYCLOAK') -> bool:
    if value is None:
        return True
    if value.lower() in ('true', '1'):
        return True
    if value.lower() in ('false', '0'):
        return False
    raise ValueError(f'{name} must be true, 1, false, or 0; empty is invalid')


ENABLE_KEYCLOAK = parse_enable_keycloak(os.getenv('ENABLE_KEYCLOAK'))
AUTH_MODE = 'keycloak' if ENABLE_KEYCLOAK else 'native'


@dataclass(frozen=True)
class NativeAuthSettings:
    web_url: str
    app_origin: str
    allow_insecure_localhost: bool
    idle_seconds: int = 1800
    absolute_seconds: int = 43200
    invitation_seconds: int = 86400
    reset_seconds: int = 1800
    recent_auth_seconds: int = 900


def _seconds(name: str, legacy_name: str, default: int) -> int:
    value = int(get_config_env(name, legacy_name, str(default)))
    if not 60 <= value <= 2592000:
        raise ValueError(f'{name} must be between 60 and 2592000 seconds')
    return value


@lru_cache(maxsize=1)
def get_native_auth_settings() -> NativeAuthSettings:
    web_url = get_web_url_from_env()
    if web_url is None:
        web_url = 'http://localhost:3000'
    try:
        parsed = urlsplit(web_url)
        # Reject malformed ports during startup instead of on the first request.
        parsed.port
    except ValueError as exc:
        raise ValueError(
            'Application web_url must have a valid HTTP(S) host and port'
        ) from exc
    insecure = parse_enable_keycloak(
        get_config_env(
            'AUTH_ALLOW_INSECURE_LOCALHOST',
            'NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST',
            'false',
        ),
        name='AUTH_ALLOW_INSECURE_LOCALHOST',
    )
    if (
        parsed.scheme not in ('https', 'http')
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or '?' in web_url
        or '#' in web_url
        or '*' in parsed.netloc
        or '\\' in web_url
        or any(character.isspace() for character in web_url)
        or parsed.netloc.endswith(':')
        or (
            parsed.scheme == 'http'
            and (
                not insecure or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            )
        )
    ):
        raise ValueError(
            'Application web_url must be a trusted HTTPS URL (or explicitly enabled localhost HTTP)'
        )
    return NativeAuthSettings(
        web_url=web_url.rstrip('/'),
        app_origin=parsed._replace(path='', query='', fragment='').geturl(),
        allow_insecure_localhost=insecure,
        idle_seconds=_seconds(
            'AUTH_SESSION_IDLE_SECONDS', 'NATIVE_AUTH_SESSION_IDLE_SECONDS', 1800
        ),
        absolute_seconds=_seconds(
            'AUTH_SESSION_ABSOLUTE_SECONDS',
            'NATIVE_AUTH_SESSION_ABSOLUTE_SECONDS',
            43200,
        ),
        invitation_seconds=_seconds(
            'AUTH_INVITATION_TTL_SECONDS', 'NATIVE_AUTH_INVITATION_TTL_SECONDS', 86400
        ),
        reset_seconds=_seconds(
            'AUTH_RESET_TTL_SECONDS', 'NATIVE_AUTH_RESET_TTL_SECONDS', 1800
        ),
    )
