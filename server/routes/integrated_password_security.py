"""Fail-closed protections for the hosted integrated password sign-in form."""

import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import Response

from server.utils.url_utils import get_web_url
from storage.redis import get_redis_client_async

TTL = 600
WINDOW = 300
COOKIE = '__Host-openhands-password-login'
_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def destination(request: Request, value: str) -> str:
    public = urlsplit(get_web_url(request))
    try:
        target = urlsplit(value)
    except ValueError:
        return '/'
    if (
        len(value) > 4096
        or '\\' in value
        or any(ord(char) < 32 for char in value)
        or value.startswith('//')
        or (
            target.netloc
            and (target.scheme, target.netloc) != (public.scheme, public.netloc)
        )
        or (target.scheme and not target.netloc)
    ):
        return '/'
    if target.netloc:
        value = target._replace(scheme='', netloc='').geturl()
    return value if value.startswith('/') else '/'


async def throttle(request: Request, *, email: str | None = None) -> None:
    ip = request.client.host if request.client else 'unknown'
    keys = (
        [(f'ip:{digest(ip)}', 30)] if email is None else [(f'email:{digest(email)}', 5)]
    )
    for key, limit in keys:
        try:
            count = await get_redis_client_async().eval(
                _INCREMENT, 1, f'integrated-password:rate:{key}', WINDOW
            )
        except Exception:
            raise HTTPException(503, 'Sign-in temporarily unavailable') from None
        if int(count) > limit:
            raise HTTPException(
                429,
                'Too many sign-in attempts. Please try again later.',
                headers={'Retry-After': str(WINDOW), 'Cache-Control': 'no-store'},
            )


async def issue(request: Request, redirect_url: str) -> str:
    if not get_web_url(request).startswith('https://'):
        raise HTTPException(503, 'Password sign-in requires HTTPS')
    nonce = secrets.token_urlsafe(32)
    try:
        await get_redis_client_async().set(
            f'integrated-password:state:{digest(nonce)}',
            destination(request, redirect_url),
            ex=TTL,
        )
    except Exception:
        raise HTTPException(503, 'Sign-in temporarily unavailable') from None
    return nonce


def protect(response: Response, nonce: str) -> None:
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.set_cookie(
        COOKIE,
        nonce,
        max_age=TTL,
        path='/',
        secure=True,
        httponly=True,
        samesite='strict',
    )


async def consume(request: Request, csrf: str) -> str:
    public = urlsplit(get_web_url(request))
    if request.headers.get('origin') != f'{public.scheme}://{public.netloc}':
        raise HTTPException(403, 'Invalid sign-in request')
    nonce = request.cookies.get(COOKIE, '')
    if not nonce or not secrets.compare_digest(nonce.encode(), csrf.encode()):
        raise HTTPException(403, 'Invalid sign-in request')
    try:
        value = await get_redis_client_async().getdel(
            f'integrated-password:state:{digest(nonce)}'
        )
    except Exception:
        raise HTTPException(503, 'Sign-in temporarily unavailable') from None
    if value is None:
        raise HTTPException(403, 'Sign-in request expired. Please reload this page.')
    return value.decode() if isinstance(value, bytes) else str(value)


async def credentials(request: Request) -> tuple[str, str, str, str]:
    if (
        request.headers.get('content-type', '').split(';', 1)[0]
        != 'application/x-www-form-urlencoded'
    ):
        raise HTTPException(415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(413)
        body.extend(chunk)
    try:
        form = parse_qs(
            body.decode(), keep_blank_values=True, max_num_fields=4, errors='strict'
        )
        if (
            not {'email', 'password'} <= set(form)
            or not set(form) <= {'email', 'password', 'redirect_url', 'csrf'}
            or any(len(values) != 1 for values in form.values())
        ):
            raise ValueError
        return (
            form['email'][0],
            form['password'][0],
            form.get('redirect_url', [''])[0],
            form.get('csrf', [''])[0],
        )
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Invalid sign-in form') from None


async def consume_invitation(token: str) -> None:
    # The signed bearer token authorizes a password reset. A used link must
    # never be usable again, even after the recipient changes their password.
    try:
        claimed = await get_redis_client_async().set(
            f'integrated-password:invite:{digest(token)}',
            'used',
            nx=True,
            ex=72 * 3600,
        )
    except Exception:
        raise HTTPException(503, 'Account setup temporarily unavailable') from None
    if not claimed:
        raise HTTPException(
            400,
            'This setup link has already been used. Ask an administrator for a new one.',
        )
