"""Native token domains and browser-cookie transport."""

import hashlib
import secrets
from urllib.parse import urlsplit

from fastapi import Request, Response

from server.auth.auth_config import get_native_auth_settings

SESSION_COOKIE = 'openhands_session'
ANONYMOUS_CSRF_COOKIE = 'openhands_csrf'


def new_token() -> str:
    return secrets.token_urlsafe(32)


def digest_token(token: str, purpose: str) -> str:
    return hashlib.sha256(f'openhands:{purpose}:{token}'.encode()).hexdigest()


def csrf_for_token(token: str) -> str:
    return digest_token(token, 'csrf-proof')


def get_app_origin() -> str:
    return get_native_auth_settings().app_origin


def _secure(request: Request) -> bool:
    config = get_native_auth_settings()
    return not (
        config.allow_insecure_localhost
        and urlsplit(config.app_origin).hostname in ('localhost', '127.0.0.1', '::1')
        and request.url.hostname in ('localhost', '127.0.0.1', '::1')
        and request.url.scheme == 'http'
    )


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    # A successful password login/setup/change establishes the browser identity.
    # Drop the legacy API-key transport without revoking the API key itself.
    clear_api_key_cookie(response, request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_native_auth_settings().absolute_seconds,
        secure=_secure(request),
        httponly=True,
        samesite='lax',
        path='/',
    )


def set_anonymous_csrf_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        ANONYMOUS_CSRF_COOKIE,
        token,
        max_age=600,
        secure=_secure(request),
        httponly=True,
        samesite='lax',
        path='/',
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path='/', secure=_secure(request), httponly=True, samesite='lax'
    )


def clear_api_key_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        'api_key', path='/', secure=_secure(request), httponly=True, samesite='lax'
    )
