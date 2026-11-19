"""Unlisted, opt-in login for pre-provisioned SaaS review accounts.

Passwords are verified by a dedicated OAuth/OIDC password-grant client, never
against the self-hosted development IDP. Successful logins use the normal
OAuth v2 token store and session, including its refresh/revocation checks.
"""

import hashlib
import os
import secrets
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_identity import map_oauth_claims, set_oauth_v2_cookie
from server.routes.auth import _get_post_auth_redirect
from server.routes.oauth_v2 import _parse_token_response
from server.routes.reviewer_login_page import render_login_page
from server.utils.url_utils import get_web_url
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.oauth_token_store import OAuthTokenStore
from storage.org_member_store import OrgMemberStore
from storage.redis import get_redis_client_async
from storage.user import ACCOUNT_TYPE_HUMAN
from storage.user_store import UserStore

LOGIN_PATH = '/oauth/reviewer/login'
STATE_TTL = 600
RATE_WINDOW = 300
router = APIRouter(include_in_schema=False)
_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


class ReviewerAccount(BaseModel):
    user_id: UUID
    org_id: UUID


_ACCOUNTS = TypeAdapter(dict[EmailStr, ReviewerAccount])


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _accounts() -> tuple[int, dict[str, ReviewerAccount]]:
    if os.getenv('ENABLE_REVIEWER_LOGIN', '').lower() != 'true':
        raise HTTPException(404)
    try:
        provider_id = int(os.environ['REVIEWER_LOGIN_PROVIDER_ID'])
        raw = _ACCOUNTS.validate_json(os.environ['REVIEWER_LOGIN_ACCOUNTS'])
        accounts = {str(email).lower(): account for email, account in raw.items()}
        if provider_id <= 0 or not accounts or len(accounts) != len(raw):
            raise ValueError
        return provider_id, accounts
    except (KeyError, ValueError, ValidationError):
        logger.error('reviewer_login_configuration_invalid')
        raise HTTPException(404) from None


async def _provider(provider_id: int) -> AuthProvider:
    provider = await AuthProviderStore().get_by_id(provider_id)
    details = provider.details if provider else None
    # A non-public, non-Git provider: never offer the review client on /login,
    # fetch Git tokens, auto-register users or match identities by email.
    if (
        provider is None
        or not provider.enabled
        or provider.is_idp
        or provider.allow_registration
        or provider.allow_match_by_email
        or not isinstance(details, OAuthProviderDetails)
        or details.provider_type != ProviderType.ENTERPRISE_SSO
        or details.legacy_keycloak_type is not None
        or details.named_secret is not None
        or not details.client_secret
        or not details.client_secret.get_secret_value()
    ):
        raise HTTPException(404)
    for url in (details.token_url, details.userinfo_url):
        parsed = urlsplit(url or '')
        if (
            parsed.scheme != 'https'
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(404)
    return provider


def _destination(value: str) -> str:
    # Reject browser URL-normalization tricks and off-origin redirects.
    if (
        len(value) > 4096
        or not value.startswith('/')
        or value.startswith('//')
        or '\\' in value
        or any(ord(char) < 32 for char in value)
        or urlsplit(value).path.startswith('/oauth/reviewer')
    ):
        return '/'
    return value


def _cookie_name(request: Request) -> str:
    return (
        '__Host-openhands-reviewer'
        if get_web_url(request).startswith('https://')
        else 'openhands-reviewer'
    )


async def _throttle(key: str, limit: int) -> None:
    try:
        count = await get_redis_client_async().eval(
            _INCREMENT, 1, f'reviewer-login:rate:{key}', RATE_WINDOW
        )
    except Exception:
        # A credential endpoint must not silently disable its protections.
        raise HTTPException(503, 'Sign-in temporarily unavailable') from None
    if int(count) > limit:
        raise HTTPException(
            429,
            'Too many sign-in attempts. Please try again later.',
            headers={'Retry-After': str(RATE_WINDOW), 'Cache-Control': 'no-store'},
        )


async def _form(
    request: Request, destination: str, *, failed: bool = False
) -> HTMLResponse:
    nonce = secrets.token_urlsafe(32)
    try:
        await get_redis_client_async().set(
            f'reviewer-login:state:{_digest(nonce)}', destination, ex=STATE_TTL
        )
    except Exception:
        raise HTTPException(503, 'Sign-in temporarily unavailable') from None
    response = render_login_page(nonce, failed=failed)
    response.set_cookie(
        _cookie_name(request),
        nonce,
        max_age=STATE_TTL,
        path='/',
        secure=get_web_url(request).startswith('https://'),
        httponly=True,
        samesite='strict',
    )
    return response


@router.get(LOGIN_PATH)
async def reviewer_login_page(request: Request, returnTo: str = '/') -> HTMLResponse:
    provider_id, _ = _accounts()
    await _provider(provider_id)
    ip = request.client.host if request.client else 'unknown'
    await _throttle(f'form:{_digest(ip)}', 60)
    return await _form(request, _destination(returnTo))


async def _verify_password(
    provider: AuthProvider, email: str, password: str
) -> tuple[dict, dict] | None:
    details = provider.details
    assert isinstance(details, OAuthProviderDetails)
    payload = {
        'grant_type': 'password',
        'client_id': details.client_id,
        'username': email,
        'password': password,
        'scope': ' '.join(details.scopes or ['openid', 'email', 'profile']),
    }
    if details.client_secret:
        payload['client_secret'] = details.client_secret.get_secret_value()
    try:
        # No redirects or TLS opt-outs on the credential submission path.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            token_response = await client.post(str(details.token_url), data=payload)
            if token_response.status_code == 400:
                return None
            if token_response.status_code != 200:
                raise ValueError
            tokens = token_response.json()
            if (
                not isinstance(tokens.get('access_token'), str)
                or not tokens['access_token']
                or not isinstance(tokens.get('refresh_token'), str)
                or not tokens['refresh_token']
                or int(tokens.get('expires_in', 0)) <= 0
            ):
                raise ValueError
            info = await client.get(
                str(details.userinfo_url),
                headers={'Authorization': f'Bearer {tokens["access_token"]}'},
            )
            if info.status_code != 200:
                raise ValueError
            claims = map_oauth_claims(info.json(), details.attribute_mapping)
            if (
                not claims.get('sub')
                or claims.get('email_verified') is not True
                or str(claims.get('email', '')).lower() != email
            ):
                return None
            return tokens, claims
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
        # Do not log passwords, tokens, claims or IdP error bodies.
        raise HTTPException(503, 'Sign-in temporarily unavailable') from None


async def _credentials(request: Request) -> tuple[str, str, str]:
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
            body.decode(), keep_blank_values=True, max_num_fields=3, errors='strict'
        )
        if set(form) != {'csrf', 'email', 'password'} or any(
            len(values) != 1 for values in form.values()
        ):
            raise ValueError
        return form['csrf'][0], form['email'][0], form['password'][0]
    except (ValueError, UnicodeError):
        raise HTTPException(400) from None


@router.post(LOGIN_PATH)
async def reviewer_login(request: Request):
    provider_id, accounts = _accounts()
    provider = await _provider(provider_id)
    origin = request.headers.get('origin')
    public = urlsplit(get_web_url(request))
    if origin != f'{public.scheme}://{public.netloc}':
        raise HTTPException(403, 'Invalid sign-in request')
    ip = request.client.host if request.client else 'unknown'
    await _throttle(f'ip:{_digest(ip)}', 30)
    csrf, email, password = await _credentials(request)
    nonce = request.cookies.get(_cookie_name(request), '')
    if not nonce or not secrets.compare_digest(nonce.encode(), csrf.encode()):
        raise HTTPException(403, 'Invalid sign-in request')
    try:
        destination = await get_redis_client_async().getdel(
            f'reviewer-login:state:{_digest(nonce)}'
        )
    except Exception:
        raise HTTPException(503, 'Sign-in temporarily unavailable') from None
    if destination is None:
        raise HTTPException(403, 'Sign-in request expired. Please reload this page.')
    if isinstance(destination, bytes):
        destination = destination.decode()
    destination = _destination(str(destination))
    email = email.strip().lower()
    if len(email) > 320 or not password or len(password) > 4096:
        return await _form(request, destination, failed=True)
    await _throttle(f'email:{_digest(email)}', 5)
    account = accounts.get(email)
    verified = await _verify_password(provider, email, password) if account else None
    if verified is None or account is None:
        return await _form(request, destination, failed=True)
    tokens, claims = verified
    link = await AuthProviderUserStore().get(provider.id, str(claims['sub']))
    user = await UserStore.get_user_by_id(str(account.user_id))
    member = await OrgMemberStore.get_org_member(account.org_id, account.user_id)
    if (
        link is None
        or link.user_id != account.user_id
        or user is None
        or user.account_type != ACCOUNT_TYPE_HUMAN
        or user.is_disabled
        or user.deletion_pending
        or not user.email_verified
        or user.role_id is not None
        or user.current_org_id != account.org_id
        or member is None
        or member.status == 'inactive'
    ):
        return await _form(request, destination, failed=True)
    access, refresh, access_exp, refresh_exp = _parse_token_response(tokens)
    await OAuthTokenStore(
        user_id=account.user_id, oauth_provider_id=provider.id
    ).store_tokens(
        access_token=access,
        refresh_token=refresh,
        access_token_expires_at=access_exp,
        refresh_token_expires_at=refresh_exp,
    )
    accepted_tos = user.accepted_tos is not None
    web_url = get_web_url(request)
    destination = await _get_post_auth_redirect(str(user.id), destination, web_url)
    if not accepted_tos:
        destination = f'{web_url}/accept-tos?redirect_url={quote(destination, safe="")}'
    response = RedirectResponse(
        destination, status_code=303, headers={'Cache-Control': 'no-store'}
    )
    set_oauth_v2_cookie(
        request=request,
        response=response,
        user_id=str(user.id),
        access_token_expires_at=access_exp,
        accepted_tos=accepted_tos,
        idp_provider_id=provider.id,
        refresh_token_expires_at=refresh_exp,
    )
    response.delete_cookie(_cookie_name(request), path='/')
    await UserStore.record_login(str(user.id))
    return response
