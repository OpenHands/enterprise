"""Installation authentication mode. This module must not import auth clients."""

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit


def parse_enable_keycloak(value: str | None) -> bool:
    if value is None:
        return True
    if value.lower() in ('true', '1'):
        return True
    if value.lower() in ('false', '0'):
        return False
    raise ValueError('ENABLE_KEYCLOAK must be true, 1, false, or 0; empty is invalid')


ENABLE_KEYCLOAK = parse_enable_keycloak(os.getenv('ENABLE_KEYCLOAK'))
AUTH_MODE = 'keycloak' if ENABLE_KEYCLOAK else 'native'


@dataclass(frozen=True)
class NativeAuthSettings:
    app_origin: str
    allow_insecure_localhost: bool
    idle_seconds: int = 1800
    absolute_seconds: int = 43200
    invitation_seconds: int = 86400
    reset_seconds: int = 1800
    recent_auth_seconds: int = 900


def _seconds(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 60 <= value <= 2592000:
        raise ValueError(f'{name} must be between 60 and 2592000 seconds')
    return value


@lru_cache(maxsize=1)
def get_native_auth_settings() -> NativeAuthSettings:
    origin = os.getenv('NATIVE_AUTH_APP_ORIGIN')
    if origin is None:
        host = os.getenv('WEB_HOST')
        origin = f'https://{host}' if host else 'http://localhost:3000'
    parsed = urlsplit(origin)
    insecure = parse_enable_keycloak(
        os.getenv('NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST', 'false')
    )
    if (
        parsed.scheme not in ('https', 'http')
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ('', '/')
        or parsed.query
        or parsed.fragment
        or (
            parsed.scheme == 'http'
            and (
                not insecure or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            )
        )
    ):
        raise ValueError(
            'NATIVE_AUTH_APP_ORIGIN must be an HTTPS origin (or explicitly enabled localhost HTTP)'
        )
    # Accessing port also rejects malformed ports rather than failing on a request.
    parsed.port
    return NativeAuthSettings(
        app_origin=origin.rstrip('/'),
        allow_insecure_localhost=insecure,
        idle_seconds=_seconds('NATIVE_AUTH_SESSION_IDLE_SECONDS', 1800),
        absolute_seconds=_seconds('NATIVE_AUTH_SESSION_ABSOLUTE_SECONDS', 43200),
        invitation_seconds=_seconds('NATIVE_AUTH_INVITATION_TTL_SECONDS', 86400),
        reset_seconds=_seconds('NATIVE_AUTH_RESET_TTL_SECONDS', 1800),
    )
