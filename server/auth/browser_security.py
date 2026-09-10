"""Browser CSRF, host-only cookies and same-origin return destinations."""

import hashlib
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import HTTPException, Request, Response

from server.auth.cookie_chunking import read_chunked_cookie
from storage.encrypt_utils import get_jwt_service

SESSION_COOKIE = 'oh_session'
CSRF_COOKIE = 'oh_csrf'


def safe_redirect(value: str | None) -> str:
    """Accept only an absolute-path reference within this application's origin."""
    value = value or '/'
    decoded = value
    # Repeated decoding rejects browser/proxy disagreements about encoded slashes.
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    try:
        parts = urlsplit(decoded)
    except ValueError:
        raise HTTPException(400, 'Invalid return destination.') from None
    if (
        len(value) > 4096
        or not decoded.startswith('/')
        or decoded.startswith('//')
        or parts.scheme
        or parts.netloc
        or '\\' in decoded
        or re.search(
            r'(?:[?&#]|^)(?:token|password|current_password|new_password|'
            r'initial_password|access_token|refresh_token|api_key|session_api_key)=',
            decoded,
            re.IGNORECASE,
        )
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        raise HTTPException(400, 'Invalid return destination.')
    return value


def auth_redirect(
    path: str, redirect_url: str, invitation_token: str | None = None
) -> str:
    params = {'returnTo': safe_redirect(redirect_url)}
    if invitation_token:
        params['invitation_token'] = invitation_token
    return f'{path}?{urlencode(params)}'


def _configured_origin() -> str:
    """Parse the existing public host configuration for browser origin checks."""
    value = os.environ.get('WEB_HOST', '').strip().rstrip('/')
    if not value:
        raise ValueError('Set WEB_HOST to the public origin.')
    if not value.startswith(('https://', 'http://')):
        value = f'https://{value}'
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ('https', 'http')
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value)
    ):
        raise ValueError('WEB_HOST must identify the public origin.')
    hostname = parsed.hostname.encode('idna').decode('ascii')
    if ':' in hostname:
        hostname = f'[{hostname}]'
    port = parsed.port
    suffix = (
        f':{port}' if port and port != (443 if parsed.scheme == 'https' else 80) else ''
    )
    return f'{parsed.scheme}://{hostname}{suffix}'


def web_origin() -> str:
    """Require a configured HTTPS origin before placing secrets in email links."""
    origin = _configured_origin()
    if not origin.startswith('https://'):
        raise ValueError(
            'WEB_HOST must identify the public HTTPS origin for account emails.'
        )
    return origin


def auth_email_configured() -> bool:
    from server.services.smtp_email_service import SMTPEmailService

    if not SMTPEmailService.is_configured():
        return False
    try:
        web_origin()
    except ValueError:
        return False
    return True


def _session_binding(request: Request) -> str:
    # Bind all browser credentials. Their explicit names distinguish values and
    # the per-cookie digests remove ambiguity without exposing any credential.
    # This also protects legacy chunked Keycloak and API-key browser sessions.
    credentials = (
        (SESSION_COOKIE, request.cookies.get(SESSION_COOKIE, '')),
        ('keycloak_auth', read_chunked_cookie(request, 'keycloak_auth') or ''),
        ('api_key', request.cookies.get('api_key', '')),
    )
    binding = '|'.join(
        f'{name}:{hashlib.sha256(value.encode()).hexdigest()}'
        for name, value in credentials
    )
    return hashlib.sha256(binding.encode()).hexdigest()


def _valid_seed(request: Request, token: str | None) -> bool:
    if not token or len(token) > 4096:
        return False
    try:
        payload = get_jwt_service().verify_jws_token(token)
        return (
            payload.get('purpose') == 'browser_csrf'
            and payload.get('session') == _session_binding(request)
            and isinstance(payload.get('nonce'), str)
        )
    except Exception:
        return False


def csrf_seed(request: Request, response: Response) -> str:
    token = request.cookies.get(CSRF_COOKIE)
    if not _valid_seed(request, token):
        token = get_jwt_service().create_jws_token(
            {
                'purpose': 'browser_csrf',
                'session': _session_binding(request),
                'nonce': secrets.token_urlsafe(32),
            },
            expires_in=timedelta(hours=24),
        )
    assert token is not None
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=86400,
        secure=True,
        httponly=False,
        samesite='lax',
        path='/',
    )
    response.headers['Cache-Control'] = 'no-store'
    return token


def validate_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, '')
    header = request.headers.get('X-CSRF-Token', '')
    origin = request.headers.get('origin')
    if not origin:
        referer = request.headers.get('referer', '')
        parts = urlsplit(referer)
        origin = f'{parts.scheme}://{parts.netloc}' if parts.netloc else None
    try:
        expected = (
            _configured_origin()
            if os.environ.get('WEB_HOST')
            else str(request.base_url).rstrip('/')
        )
    except ValueError:
        raise HTTPException(403, 'Invalid request origin configuration.') from None
    if (
        origin != expected
        or not cookie
        or not header
        or not cookie.isascii()
        or not header.isascii()
        or not secrets.compare_digest(cookie, header)
        or not _valid_seed(request, cookie)
    ):
        raise HTTPException(403, 'Invalid CSRF token or request origin.')


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=86400,
        secure=True,
        httponly=True,
        samesite='lax',
        path='/',
    )
    # The next CSRF GET binds a fresh seed to the new authenticated session.
    response.delete_cookie(
        CSRF_COOKIE, secure=True, httponly=False, samesite='lax', path='/'
    )
    response.headers['Cache-Control'] = 'no-store'


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, secure=True, httponly=True, samesite='lax', path='/'
    )
    response.delete_cookie(
        CSRF_COOKIE, secure=True, httponly=False, samesite='lax', path='/'
    )
    response.headers['Cache-Control'] = 'no-store'
