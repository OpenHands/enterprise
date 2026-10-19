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

Non-OAuth2 ``kind``s hijack ``/login``/``/callback`` by provider kind rather
than getting their own top-level entry point, so every IDP row -- regardless
of protocol -- stays reachable the same uniform way (``/oauth/{id}/login``,
and the kind-agnostic ``/oauth/idp-login`` convenience redirect). The
integrated password IDP (``server.routes.idp``) was first; SAML
(``server.routes.saml``) follows the same pattern -- ``/login`` redirects to
the SAML router's own SP-initiated endpoint, and ``/callback`` 404s since
SAML's ACS is a POST, handled there instead. Identity resolution and the
``openhands_auth`` cookie (``server.auth.provider_identity``) are shared
across every kind; only the handshake used to reach that shared cookie-minting
step differs.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_identity import (
    decrypt_state as _decrypt_state,
)
from server.auth.provider_identity import (
    encrypt_state as _encrypt_state,
)
from server.auth.provider_identity import (
    get_provider_or_404 as _get_provider,
)
from server.auth.provider_identity import (
    resolve_or_create_user as _resolve_or_create_user,
)
from server.auth.provider_identity import (
    set_oauth_v2_cookie as _set_oauth_v2_cookie,
)
from server.auth.token_manager import TokenManager
from server.utils.url_utils import get_web_url
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import (
    OAuthProviderDetails,
    PasswordProviderDetails,
    SamlProviderDetails,
)
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.oauth_token_store import OAuthTokenStore
from storage.user_store import UserStore

# HTTP timeout for external OAuth/token endpoint calls.
OAUTH_HTTP_TIMEOUT = 15.0

oauth_v2_router = APIRouter(prefix='/oauth', tags=['OAuth v2'])


class OAuthState(BaseModel):
    """The encrypted OAuth state payload."""

    redirect_url: str = ''
    mode: Literal['login', 'link'] = 'login'
    # Set on the link flow's return leg so the callback knows which user is
    # linking. Absent on login flows.
    user_id: str | None = None
    nonce: str = ''


# ── helpers ──────────────────────────────────────────────────────────────


def _require_oauth_provider(provider: AuthProvider) -> None:
    """404 unless ``provider`` is generic OAuth2.

    Fails closed for any provider kind without a dedicated branch before the
    call, so a newly added kind can't fall through into the OAuth2 flow.
    """
    if not isinstance(provider.details, OAuthProviderDetails):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Provider {provider.id} does not support OAuth2 login',
        )


def _oauth_details(provider: AuthProvider) -> OAuthProviderDetails:
    """Narrow ``provider.details`` to ``OAuthProviderDetails``.

    Every caller of this helper runs after ``oauth_v2_login`` /
    ``oauth_v2_callback`` have excluded non-OAuth2 rows (see
    ``_require_oauth_provider``), so this should never actually mismatch -- the
    assertion documents that invariant instead of silently reaching into the
    wrong subclass's fields.
    """
    assert isinstance(provider.details, OAuthProviderDetails)
    return provider.details


async def _exchange_code(provider: AuthProvider, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for a token response dict."""
    details = _oauth_details(provider)
    token_url = details.token_url
    if not token_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Provider {provider.id} has no token_url',
        )
    payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': details.client_id,
    }
    client_secret = (
        details.client_secret.get_secret_value() if details.client_secret else None
    )
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


async def _fetch_userinfo(provider: AuthProvider, access_token: str) -> dict:
    """Call the provider userinfo endpoint with the access token."""
    details = _oauth_details(provider)
    if not details.userinfo_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Provider {provider.id} has no userinfo_url',
        )
    async with httpx.AsyncClient(
        verify=httpx_verify_option(), timeout=OAUTH_HTTP_TIMEOUT
    ) as client:
        response = await client.get(
            details.userinfo_url,
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


def _epoch_to_datetime(expires_at: int | str | None) -> datetime | None:
    """Convert a broker-response epoch timestamp to a tz-aware datetime.

    ``get_idp_tokens_from_keycloak`` uses ``0`` for "never expires" (no
    ``0`` epoch-second sentinel is otherwise reachable); the rest of
    oauth_v2 uses ``None`` for the same thing -- see ``_parse_token_response``.
    """
    if not expires_at:
        return None
    return datetime.fromtimestamp(int(expires_at), tz=timezone.utc)


async def _store_legacy_keycloak_git_token(
    provider_type: ProviderType,
    user_id: uuid.UUID,
    keycloak_access_token: str,
) -> None:
    """Fetch ``provider_type``'s real token via Keycloak's identity-brokering
    endpoint and store it under the git-provider row of that type.

    Called after a successful OAuth2 exchange against a login row with
    ``OAuthProviderDetails.legacy_keycloak_type`` set: that row's own
    ``token_url`` is Keycloak's, so the pair ``_exchange_code`` just
    returned is a Keycloak access/refresh token, never ``provider_type``'s
    real one -- Keycloak does not embed the upstream IdP's token in its own
    OIDC response, it only exposes it via this separate endpoint.

    Best-effort: any failure here is logged and swallowed rather than
    failing the login/link itself, which already completed successfully
    with the (perfectly valid, for its own purpose) Keycloak token pair
    persisted under the login row -- a transient broker/network error
    should not lock the user out of the app, only leave git-provider repo
    access unavailable until the next login (or a manual link in
    Settings > Integrations).
    """
    git_provider = await AuthProviderStore().get_provider_by_type(provider_type.value)
    if git_provider is None:
        # Should not happen in practice -- migration 189 only seeds a
        # legacy-Keycloak login row when the matching git-provider row
        # (migration 168) already exists, gated on the same env var -- but an
        # admin could have deleted that row since, or hand-created a login
        # row via the admin API without one.
        logger.warning(
            'oauth_v2_legacy_keycloak_no_git_provider_row',
            extra={'provider_type': provider_type.value},
        )
        return

    try:
        data = await TokenManager().get_idp_tokens_from_keycloak(
            keycloak_access_token, provider_type
        )
    except Exception:
        logger.exception(
            'oauth_v2_legacy_keycloak_broker_fetch_failed',
            extra={'provider_type': provider_type.value},
        )
        return
    if not data:
        # The broker reported no association for this IdP (e.g. the user
        # was created by some other means and never actually went through
        # Keycloak's GitHub/GitLab/Bitbucket federation).
        logger.info(
            'oauth_v2_legacy_keycloak_no_broker_token',
            extra={'provider_type': provider_type.value},
        )
        return

    await OAuthTokenStore(
        user_id=user_id, oauth_provider_id=git_provider.id
    ).store_tokens(
        access_token=str(data['access_token']),
        refresh_token=str(data['refresh_token']),
        access_token_expires_at=_epoch_to_datetime(data.get('access_token_expires_at')),
        refresh_token_expires_at=_epoch_to_datetime(
            data.get('refresh_token_expires_at')
        ),
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
    provider = await AuthProviderStore().get_first_idp()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No IDP OAuth provider is configured',
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

    # Integrated IDP is handled by its own routes (registered before this
    # router): intercept by kind and redirect to its dedicated email+password
    # login page instead of building an external OAuth URL.
    if provider.kind == PasswordProviderDetails.__name__:
        from server.routes.idp import IDP_LOGIN_PATH

        web_url = get_web_url(request)
        target = f'{web_url}/oauth/{IDP_LOGIN_PATH}'
        params: dict[str, str] = {}
        if redirect_url:
            params['redirect_url'] = redirect_url
        if mode and mode != 'login':
            params['mode'] = mode
        if params:
            target = f'{target}?{urlencode(params)}'
        return RedirectResponse(target, status_code=302)

    # SAML IDPs speak a different protocol end-to-end (signed AuthnRequest,
    # POST-bound ACS) -- handed off to their own router (``server.routes.saml``)
    # rather than building an OAuth2 authorization URL below.
    if provider.kind == SamlProviderDetails.__name__:
        web_url = get_web_url(request)
        target = f'{web_url}/saml/{provider_id}/login'
        params = {}
        if redirect_url:
            params['redirect_url'] = redirect_url
        if params:
            target = f'{target}?{urlencode(params)}'
        return RedirectResponse(target, status_code=302)

    _require_oauth_provider(provider)
    details = _oauth_details(provider)
    auth_url = details.authorization_url
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
        'client_id': details.client_id,
        'redirect_uri': callback_uri,
        'response_type': 'code',
        'state': encrypted_state,
    }
    scopes = details.scopes or []
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
    provider = await _get_provider(provider_id)

    # Password and SAML callbacks are handled by dedicated routes; checked
    # first, unconditionally, so this 404s on provider kind alone regardless
    # of whatever ``code``/``state`` a caller happens to send.
    _require_oauth_provider(provider)

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
        await AuthProviderUserStore().link(
            oauth_provider_id=provider.id,
            user_id=uuid.UUID(user_id),
            external_subject_id=subject,
        )
    else:
        userinfo = await _fetch_userinfo(provider, access_token)
        user_id = await _resolve_or_create_user(provider, userinfo)
        if await UserStore.is_user_disabled(user_id):
            return RedirectResponse(
                f'{web_url}/login?account_disabled=true', status_code=302
            )

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

    # ``provider`` is a login IDP whose own token exchange only ever yields
    # a Keycloak token (see ``OAuthProviderDetails.legacy_keycloak_type``) --
    # additionally fetch and store the real git-provider token so repo
    # access works the same way it did for the pre-OAuth-v2 Keycloak login
    # path (``TokenManager.store_idp_tokens``), not just the login session.
    details = provider.details
    if (
        isinstance(details, OAuthProviderDetails)
        and details.legacy_keycloak_type is not None
    ):
        await _store_legacy_keycloak_git_token(
            provider_type=details.legacy_keycloak_type,
            user_id=uuid.UUID(user_id),
            keycloak_access_token=access_token,
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
            idp_provider_id=provider.id,
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

    Removes the ``auth_provider_users`` link and the stored ``oauth_tokens``
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
    link_store = AuthProviderUserStore()
    link = await link_store.get_by_user(user_uuid, provider.id)
    if link is not None:
        await link_store.unlink(provider.id, link.external_subject_id)
    token_store = OAuthTokenStore(user_id=user_uuid, oauth_provider_id=provider.id)
    await token_store.delete_tokens()
    return None


@oauth_v2_router.get('/providers')
async def oauth_v2_list_providers():
    """List configured OAuth providers (Phase 1 introspection/debug)."""
    store = AuthProviderStore()
    providers = await store.list_all()
    return {
        'providers': [
            {
                'id': p.id,
                'kind': p.kind,
                'title': p.title,
                'is_idp': p.is_idp,
            }
            for p in providers
        ]
    }


class PublicIdpProvider(BaseModel):
    """A single login IDP, as shown on the unauthenticated "Let's Get
    Started" screen -- see ``oauth_v2_list_idp_providers``. Never carries
    anything from ``details`` beyond the OAuth brand (``provider_type``),
    which is not sensitive and only used to pick a fallback icon client-side
    when ``icon`` is unset.
    """

    id: int
    title: str
    icon: str | None
    color: str
    provider_type: str | None


@oauth_v2_router.get('/idp-providers', response_model=list[PublicIdpProvider])
async def oauth_v2_list_idp_providers() -> list[PublicIdpProvider]:
    """Public, unauthenticated list of configured login IDPs.

    Backs the "Let's Get Started" login screen when
    ``ENABLE_OAUTH_V2_LOGIN`` is on: unlike ``GET /oauth/idp-login`` (which
    silently picks one IDP and redirects), this lets the frontend render a
    button per configured IDP -- using each row's own branding -- so an
    admin who has configured more than one login IDP doesn't lose the
    "choose how to sign in" screen the legacy (Keycloak-era) ``/login`` page
    always had.

    Deliberately distinct from ``GET /api/admin/oauth-providers`` (admin,
    permission-gated, full ``details`` with masked secrets) and from ``GET
    /oauth/providers`` above (unauthenticated, but a debug/introspection
    endpoint not meant for product UI and not filtered to IDPs): this one is
    the only "safe to point untrusted/unauthenticated UI at" shape -- no
    secrets, no non-IDP (git provider) rows, no ``client_id``/URLs.
    """
    providers = await AuthProviderStore().get_idp_providers()
    return [
        PublicIdpProvider(
            id=p.id,
            title=p.title,
            icon=p.icon,
            color=p.color,
            provider_type=(
                p.details.provider_type.value
                if isinstance(p.details, OAuthProviderDetails)
                else None
            ),
        )
        for p in providers
    ]
