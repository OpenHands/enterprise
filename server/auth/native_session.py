"""Native token domains and browser-cookie transport."""

import hashlib
import secrets
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi_users.authentication import CookieTransport

from server.auth.auth_config import get_native_auth_settings

SESSION_COOKIE = 'openhands_session'


def new_token() -> str:
    return secrets.token_urlsafe(32)


def digest_token(token: str, purpose: str) -> str:
    return hashlib.sha256(f'openhands:{purpose}:{token}'.encode()).hexdigest()


def get_app_origin() -> str:
    return get_native_auth_settings().app_origin


def secure_cookies(request: Request) -> bool:
    config = get_native_auth_settings()
    return not (
        config.allow_insecure_localhost
        and urlsplit(config.app_origin).hostname in ('localhost', '127.0.0.1', '::1')
        and request.url.hostname in ('localhost', '127.0.0.1', '::1')
        and request.url.scheme == 'http'
    )


def cookie_transport(request: Request) -> CookieTransport:
    return CookieTransport(
        cookie_name=SESSION_COOKIE, cookie_secure=secure_cookies(request)
    )


async def set_session_cookie(response: Response, token: str, request: Request) -> None:
    from server.auth.native_csrf import clear_csrf_cookie

    clear_api_key_cookie(response, request)
    clear_csrf_cookie(request, response)
    login = await cookie_transport(request).get_login_response(token)
    response.headers.append('set-cookie', login.headers['set-cookie'])


async def clear_session_cookie(response: Response, request: Request) -> None:
    logout = await cookie_transport(request).get_logout_response()
    response.headers.append('set-cookie', logout.headers['set-cookie'])


def clear_api_key_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        'api_key',
        path='/',
        secure=secure_cookies(request),
        httponly=True,
        samesite='lax',
    )
