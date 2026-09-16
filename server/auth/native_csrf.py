"""Signed double-submit CSRF protection for the OpenHands browser transport."""

from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException, Request, Response
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

from server.auth.native_session import SESSION_COOKIE, secure_cookies
from storage.encrypt_utils import get_jwt_service

CSRF_COOKIE = '__Host-openhands_csrf'
LOCAL_CSRF_COOKIE = 'openhands_csrf'
CSRF_MAX_AGE = 600


@lru_cache(maxsize=2)
def _protector(secure: bool) -> CsrfProtect:
    # load_config mutates class attributes. Each fixed cookie policy gets its
    # own class so localhost requests cannot relax another request's cookies.
    class BrowserCsrfProtect(CsrfProtect):
        pass

    def settings() -> list[tuple[str, str | bool | int]]:
        return [
            ('cookie_key', CSRF_COOKIE if secure else LOCAL_CSRF_COOKIE),
            ('cookie_path', '/'),
            ('cookie_secure', secure),
            ('cookie_samesite', 'lax'),
            ('httponly', True),
            ('header_name', 'X-CSRF-Token'),
            ('token_location', 'header'),
            ('max_age', CSRF_MAX_AGE),
        ]

    BrowserCsrfProtect.load_config(settings)
    return BrowserCsrfProtect()


def _signing_key(session_token: str | None) -> str:
    # Reuse the persistent application key; HKDF domain-separates CSRF and binds
    # the library's signatures to the current browser session. Login, password
    # changes and logout therefore invalidate proof from the previous identity.
    jwt_service = get_jwt_service()
    master = jwt_service.get_key(jwt_service.default_key_id).key.get_secret_value()
    return (
        HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=session_token.encode() if session_token else None,
            info=b'openhands:browser-csrf',
        )
        .derive(master.encode())
        .hex()
    )


def issue_csrf(request: Request, response: Response, session_token: str | None) -> str:
    protector = _protector(secure_cookies(request))
    token, signed_token = protector.generate_csrf_tokens(_signing_key(session_token))
    protector.set_csrf_cookie(signed_token, response)
    return token


async def validate_csrf(request: Request) -> None:
    try:
        await _protector(secure_cookies(request)).validate_csrf(
            request, secret_key=_signing_key(request.cookies.get(SESSION_COOKIE))
        )
    except CsrfProtectError as exc:
        # The browser retries only this exact pre-dispatch failure after fetching
        # fresh proof, including when a second tab has renewed the shared cookie.
        raise HTTPException(403, 'Invalid CSRF token') from exc


def clear_csrf_cookie(request: Request, response: Response) -> None:
    _protector(secure_cookies(request)).unset_csrf_cookie(response)
