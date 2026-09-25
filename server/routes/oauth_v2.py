"""Phase 1 OAuth v2 callback routes — additive, not yet the default login path.

New endpoints:

- ``GET /oauth/{provider_id}/login`` — start an OAuth flow at a configured
  provider, encoding an encrypted state blob that carries the redirect URL and
  flow mode (``login`` or ``link``).
- ``GET /oauth/{provider_id}/callback`` — provider redirect target; exchanges
  the code for tokens, persists them via the new stores, and (for login) sets
  the new small JWT cookie.
- ``POST /oauth/{provider_id}/link`` — link a git provider to the signed-in
  user (must be authenticated).
- ``DELETE /oauth/{provider_id}`` — unlink a provider from the signed-in user.

These routes are registered but do NOT replace the Keycloak login flow yet
(Phase 2 dual-cookie middleware switches new logins over).
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.encrypt_utils import get_jwt_service
from storage.oauth_provider import OAuthProvider
from storage.oauth_provider_store import OAuthProviderStore
from storage.oauth_provider_user_store import OAuthProviderUserStore
from storage.oauth_token_store import OAuthTokenStore
from storage.user_store import UserStore

# HTTP timeout for external OAuth/token endpoint calls.
OAUTH_HTTP_TIMEOUT = 15.0

oauth_v2_router = APIRouter(prefix='/oauth', tags=['OAuth v2'])


# ── encrypted state ──────────────────────────────────────────────────────


def _encrypt_state(payload: dict) -> str:
    """Encrypt the OAuth state blob (JWE) and base64-encode for URL transport."""
    raw = json.dumps(payload)
    encrypted = get_jwt_service().encrypt_value(raw)
    return base64.urlsafe_b64encode(encrypted.encode()).decode()


def _decrypt_state(state: str) -> dict:
    """Reverse of ``_encrypt_state``."""
    encrypted = base64.urlsafe_b64decode(state.encode()).decode()
    raw = get_jwt_service().decrypt_value(encrypted)
    return json.loads(raw)


class OAuthState(BaseModel):
    """The encrypted OAuth state payload."""

    redirect_url: str = ''
    mode: Literal['login', 'link'] = 'login'
    # Set on the link flow's return leg so the callback knows which user is
    # linking. Absent on login flows.
    user_id: str | None = None
    nonce: str = ''


# ── helpers ──────────────────────────────────────────────────────────────


async def _get_provider(provider_id: int) -> OAuthProvider:
    store = OAuthProviderStore()
    provider = await store.get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'OAuth provider {provider_id} not found',
        )
    return provider


def _secret_str(provider: OAuthProvider) -> str | None:
    blob = provider.client_secret
    if blob is None:
        return None
    return blob.get('v') if isinstance(blob, dict) else str(blob)


async def _exchange_code(provider: OAuthProvider, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for a token response dict."""
    token_url = provider.token_url
    if not token_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Provider {provider.id} has no token_url',
        )
    payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': provider.client_id,
    }
    client_secret = _secret_str(provider)
    if client_secret:
        payload['client_secret'] = client_secret
    async with httpx.AsyncClient(
        verify=httpx_verify_option(), timeout=OAUTH_HTTP_TIMEOUT
    ) as client:
        response = await client.post(
            token_url,
            data=payload,
            headers={'Accept': 'application/json'},
        )
        if response.status_code >= 400:
            logger.warning(
                'oauth_v2 token exchange failed provider=%s status=%s body=%s',
                provider.id,
                response.status_code,
                response.text,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail='Token exchange failed at the provider',
            )
        # Some providers return url-encoded form bodies.
        content_type = response.headers.get('content-type', '')
        if 'application/json' in content_type:
            return response.json()
        from urllib.parse import parse_qs

        parsed = parse_qs(response.text)
        return {k: v[0] for k, v in parsed.items()}


def _parse_token_response(
    token_data: dict,
) -> tuple[str, str | None, datetime | None, datetime | None]:
    """Normalize a provider token response.

    Returns (access_token, refresh_token, access_expires_at, refresh_expires_at).
    ``None`` expiry means "never expires".
    """
    access_token = str(token_data['access_token'])
    refresh_token = token_data.get('refresh_token')
    refresh_token = str(refresh_token) if refresh_token else None

    now = datetime.now(timezone.utc)
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    if 'expires_in' in token_data:
        try:
            access_expires_at = now + timedelta(seconds=int(token_data['expires_in']))
        except (TypeError, ValueError):
            pass
    for key in ('refresh_token_expires_in', 'refresh_expires_in'):
        if key in token_data:
            try:
                refresh_expires_at = now + timedelta(seconds=int(token_data[key]))
            except (TypeError, ValueError):
                pass
            break
    return access_token, refresh_token, access_expires_at, refresh_expires_at


async def _fetch_userinfo(provider: OAuthProvider, access_token: str) -> dict:
    """Call the provider userinfo endpoint with the access token."""
    if not provider.userinfo_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Provider {provider.id} has no userinfo_url',
        )
    async with httpx.AsyncClient(
        verify=httpx_verify_option(), timeout=OAUTH_HTTP_TIMEOUT
    ) as client:
        response = await client.get(
            provider.userinfo_url,
            headers={'Authorization': f'Bearer {access_token}'},
        )
        if response.status_code >= 400:
            logger.warning(
                'oauth_v2 userinfo failed provider=%s status=%s',
                provider.id,
                response.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail='Userinfo lookup failed at the provider',
            )
        return response.json()


async def _resolve_or_create_user(provider: OAuthProvider, userinfo: dict) -> str:
    """Resolve the internal user for a provider userinfo dict.

    Looks up ``oauth_provider_users`` by ``external_subject_id`` (the OIDC
    ``sub``). If found, returns the linked user id. Otherwise creates the user
    and links it. Phase 1 additive behavior; the real user-mgmt migration
    (Phase 3) backfills from Keycloak.
    """
    subject = userinfo.get('sub') or userinfo.get('id') or userinfo.get('user_id')
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Provider did not return a subject identifier',
        )
    subject = str(subject)
    link_store = OAuthProviderUserStore()
    existing = await link_store.get(provider.id, subject)
    if existing is not None:
        return str(existing.user_id)

    user_info_dict = {
        'email': userinfo.get('email'),
        'email_verified': userinfo.get('email_verified'),
    }
    user = await UserStore.create_user(subject, user_info_dict)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to create user from OAuth userinfo',
        )
    await link_store.link(
        oauth_provider_id=provider.id,
        user_id=user.id,
        external_subject_id=subject,
        external_email=userinfo.get('email'),
    )
    return str(user.id)


def _set_oauth_v2_cookie(
    request: Request,
    response: Response,
    user_id: str,
    access_token_expires_at: datetime | None,
    accepted_tos: bool,
    refresh_token_expires_at: datetime | None = None,
) -> None:
    """Set the small JWT cookie for the OAuth v2 path.

    The cookie carries only ``user_id``, ``access_token_expires_at``, and
    ``accepted_tos`` — not the tokens themselves. ``Max-Age`` is the IDP
    refresh-token expiry (or a 30-day cap if ``None``), per the Phase 2 spec.
    """
    from server.auth.oauth_v2_refresh import (
        compute_cookie_max_age,
        create_oauth_v2_cookie_payload,
        sign_oauth_v2_cookie,
    )

    max_age_seconds = compute_cookie_max_age(refresh_token_expires_at)
    payload = create_oauth_v2_cookie_payload(
        user_id, access_token_expires_at, accepted_tos
    )
    signed = sign_oauth_v2_cookie(payload, max_age_seconds)
    web_url = get_web_url(request)
    response.set_cookie(
        key='openhands_auth',
        value=signed,
        max_age=max_age_seconds,
        domain=get_cookie_domain(),
        secure=web_url.startswith('https'),
        httponly=True,
        samesite=get_cookie_samesite(),
    )


# ── routes ────────────────────────────────────────────────────────────────


def _login_redirect_url(
    request: Request, provider_id: int, redirect_url: str, mode: str
) -> str:
    """Build the canonical ``/oauth/{provider_id}/login`` URL, forwarding the
    ``redirect_url`` and ``mode`` query params so the underlying login flow
    receives them."""
    web_url = get_web_url(request)
    target = f'{web_url}/oauth/{provider_id}/login'
    params: dict[str, str] = {}
    if redirect_url:
        params['redirect_url'] = redirect_url
    if mode and mode != 'login':
        params['mode'] = mode
    if params:
        target = f'{target}?{urlencode(params)}'
    return target


@oauth_v2_router.get('/idp-login')
async def oauth_v2_idp_login(
    request: Request,
    redirect_url: str = '',
    mode: str = 'login',
):
    """Redirect to ``/oauth/<id>/login`` of the first available IDP provider.

    Convenience entry point so callers do not need to know the concrete OAuth
    provider ID. Returns ``404`` when no IDP provider is configured.
    """
    provider = await OAuthProviderStore().get_first_idp()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No IDP OAuth provider is configured',
        )
    return RedirectResponse(
        _login_redirect_url(request, provider.id, redirect_url, mode),
        status_code=302,
    )


@oauth_v2_router.get('/{provider_type}-login')
async def oauth_v2_provider_type_login(
    request: Request,
    provider_type: str,
    redirect_url: str = '',
    mode: str = 'login',
):
    """Redirect to ``/oauth/<id>/login`` of the first provider of a given type.

    ``provider_type`` is the ``provider_category`` value (e.g. ``github``) taken
    from the URL segment before ``-login`` — e.g. ``/oauth/github-login``.
    Returns ``404`` when no matching provider exists.
    """
    provider = await OAuthProviderStore().get_first_by_category(provider_type)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'No OAuth provider found for type {provider_type!r}',
        )
    return RedirectResponse(
        _login_redirect_url(request, provider.id, redirect_url, mode),
        status_code=302,
    )


@oauth_v2_router.get('/{provider_id}/login')
async def oauth_v2_login(
    request: Request,
    provider_id: int,
    redirect_url: str = '',
    mode: Literal['login', 'link'] = 'login',
):
    """Start an OAuth flow at the configured provider.

    Encodes an encrypted state blob (redirect URL, mode, nonce) and redirects
    the browser to the provider's authorization URL.
    """
    provider = await _get_provider(provider_id)
    auth_url = provider.authorization_url
    if not auth_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Provider {provider_id} has no authorization_url',
        )

    state = OAuthState(
        redirect_url=redirect_url,
        mode=mode,
        nonce=str(time.time()),
    )
    encrypted_state = _encrypt_state(state.model_dump())

    web_url = get_web_url(request)
    callback_uri = f'{web_url}/oauth/{provider_id}/callback'

    params = {
        'client_id': provider.client_id,
        'redirect_uri': callback_uri,
        'response_type': 'code',
        'state': encrypted_state,
    }
    scopes = provider.scopes or []
    if scopes:
        params['scope'] = ' '.join(scopes)

    separator = '&' if '?' in auth_url else '?'
    return RedirectResponse(f'{auth_url}{separator}{urlencode(params)}')


@oauth_v2_router.get('/{provider_id}/callback')
async def oauth_v2_callback(
    request: Request,
    provider_id: int,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Provider redirect target for the new OAuth path.

    Exchanges the code, persists tokens, and either links the provider to the
    signed-in user (``link`` mode) or completes a login (``login`` mode).
    """
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'OAuth provider returned error: {error}',
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing code or state in OAuth callback',
        )

    try:
        state_data = OAuthState(**_decrypt_state(state))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid or tampered OAuth state',
        ) from exc

    provider = await _get_provider(provider_id)
    web_url = get_web_url(request)
    redirect_uri = f'{web_url}/oauth/{provider_id}/callback'

    token_data = await _exchange_code(provider, code, redirect_uri)
    access_token, refresh_token, access_exp, refresh_exp = _parse_token_response(
        token_data
    )

    if state_data.mode == 'link':
        if not state_data.user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Link flow missing user_id in state',
            )
        user_id = state_data.user_id
        # Best-effort userinfo for the external subject link.
        try:
            userinfo = await _fetch_userinfo(provider, access_token)
            subject = str(userinfo.get('sub') or userinfo.get('id') or user_id)
        except HTTPException:
            subject = user_id
        await OAuthProviderUserStore().link(
            oauth_provider_id=provider.id,
            user_id=uuid.UUID(user_id),
            external_subject_id=subject,
        )
    else:
        userinfo = await _fetch_userinfo(provider, access_token)
        user_id = await _resolve_or_create_user(provider, userinfo)

    token_store = OAuthTokenStore(
        user_id=uuid.UUID(user_id),
        oauth_provider_id=provider.id,
    )
    await token_store.store_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_at=access_exp,
        refresh_token_expires_at=refresh_exp,
    )

    redirect_url = state_data.redirect_url or '/'

    if state_data.mode == 'login':
        # Mirror the legacy Keycloak callback: derive ``accepted_tos`` from
        # the user row so the cookie reflects the real TOS state, and
        # redirect to the TOS page when it has not been accepted yet.
        user = await UserStore.get_user_by_id(user_id)
        has_accepted_tos = user is not None and user.accepted_tos is not None
        if not has_accepted_tos:
            encoded_redirect_url = quote(redirect_url, safe='')
            redirect_url = f'{web_url}/accept-tos?redirect_url={encoded_redirect_url}'
        response = RedirectResponse(redirect_url, status_code=302)
        _set_oauth_v2_cookie(
            request=request,
            response=response,
            user_id=user_id,
            access_token_expires_at=access_exp,
            accepted_tos=has_accepted_tos,
            refresh_token_expires_at=refresh_exp,
        )
    else:
        response = RedirectResponse(redirect_url, status_code=302)
    return response


@oauth_v2_router.post('/{provider_id}/link')
async def oauth_v2_link(
    request: Request,
    provider_id: int,
    user_id: str = Depends(get_user_id),
):
    """Link a git provider to the signed-in user.

    Returns a JSON body with a ``start_url`` to ``/oauth/{provider_id}/login``
    in link mode, which the frontend navigates the browser to. The callback
    completes the link.
    """
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required to link a provider',
        )
    provider = await _get_provider(provider_id)
    if provider.is_idp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot link an IDP provider; use login instead',
        )
    web_url = get_web_url(request)
    link_state = OAuthState(
        redirect_url=f'{web_url}/settings/integrations',
        mode='link',
        user_id=user_id,
        nonce=str(time.time()),
    )
    encrypted = _encrypt_state(link_state.model_dump())
    start_url = (
        f'{web_url}/oauth/{provider_id}/login'
        f'?mode=link&redirect_url={web_url}/settings/integrations'
        f'&_state={encrypted}'
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={'start_url': start_url},
    )


@oauth_v2_router.delete('/{provider_id}', status_code=status.HTTP_204_NO_CONTENT)
async def oauth_v2_unlink(
    provider_id: int,
    user_id: str = Depends(get_user_id),
):
    """Unlink a provider from the signed-in user.

    Removes the ``oauth_provider_users`` link and the stored ``oauth_tokens``
    row. The v2 replacement for ``kc_action=idp_link`` / ``unlink_idp``.
    """
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required to unlink a provider',
        )
    provider = await _get_provider(provider_id)
    if provider.is_idp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot unlink an IDP provider',
        )

    user_uuid = uuid.UUID(user_id)
    link_store = OAuthProviderUserStore()
    link = await link_store.get_by_user(user_uuid, provider.id)
    if link is not None:
        await link_store.unlink(provider.id, link.external_subject_id)
    token_store = OAuthTokenStore(user_id=user_uuid, oauth_provider_id=provider.id)
    await token_store.delete_tokens()
    return None


@oauth_v2_router.get('/providers')
async def oauth_v2_list_providers():
    """List configured OAuth providers (Phase 1 introspection/debug)."""
    store = OAuthProviderStore()
    providers = await store.list_all()
    return {
        'providers': [
            {
                'id': p.id,
                'provider_category': p.provider_category,
                'display_name': p.display_name,
                'is_idp': p.is_idp,
            }
            for p in providers
        ]
    }
