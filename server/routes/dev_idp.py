"""Development-only insecure IDP — pretends to be a regular OAuth v2 IDP.

This module provides a login path that does **not** depend on Keycloak or any
external identity provider. It is intended for self-hosted / trial installs
that have no real IDP configured yet.

**Design**: the dev IDP plugs into the existing OAuth v2 flow as if it were a
regular IDP provider. When no real IDP is configured in ``oauth_providers``
and the deployment is self-hosted:

* ``OAuthProviderStore.get_first_idp()`` returns a synthetic ``DevIdpProvider``
  sentinel (id = ``DEV_IDP_PROVIDER_ID``).
* ``GET /oauth/idp-login`` redirects to ``/oauth/{DEV_IDP_PROVIDER_ID}/login``,
  which is intercepted by this module to serve an HTML email-entry form.
* The form ``POST``s to ``/oauth/{DEV_IDP_PROVIDER_ID}/callback``, which
  completes the login (user creation, cookie, redirect) using the same
  ``openhands_auth`` JWT cookie as the real OAuth v2 callback.

When a real IDP is configured, the sentinel is not returned and the dev IDP
endpoints return ``404``.

**This IDP is intentionally insecure.** It must never be enabled on cloud
(``app.all-hands.dev``) or any deployment where security matters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import EmailStr

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.oauth_v2_refresh import (
    create_oauth_v2_cookie_payload,
    sign_oauth_v2_cookie,
)
from server.constants import DEPLOYMENT_MODE
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.default_org_service import DefaultOrgBootstrapService
from storage.oauth_provider_store import OAuthProviderStore
from storage.user_store import UserStore

dev_idp_router = APIRouter(prefix='/oauth', tags=['Dev IDP'])

# Sentinel provider ID used by the dev IDP.  Negative so it can never collide
# with a real DB row (Identity columns start at 1).
DEV_IDP_PROVIDER_ID = -1
DEV_IDP_CATEGORY = 'dev_idp'

# Fixed namespace for deterministic user-id derivation from email.
_DEV_IDP_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890')


@dataclass(frozen=True)
class DevIdpProvider:
    """Sentinel that quacks like ``OAuthProvider`` for the OAuth v2 flow.

    Returned by ``OAuthProviderStore.get_first_idp()`` when no real IDP is
    configured on a self-hosted deployment. The OAuth v2 routes check
    ``provider.id == DEV_IDP_PROVIDER_ID`` to intercept and redirect to the
    dev IDP email-entry form instead of building an external OAuth URL.
    """

    id: int = DEV_IDP_PROVIDER_ID
    provider_category: str = DEV_IDP_CATEGORY
    display_name: str = 'Development IDP'
    is_idp: bool = True
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: list[str] | None = None
    client_id: str = 'dev-idp'
    client_secret: dict[str, str] | None = None


def derive_dev_idp_user_id(email: str) -> str:
    """Derive a deterministic UUID from an email address.

    Uses ``uuid.uuid5`` (SHA-1 based) so the same email always maps to the
    same user id. The email is lower-cased and stripped before hashing so
    ``Alice@Example.COM`` and ``alice@example.com`` resolve to the same user.
    """
    normalized = email.strip().lower()
    return str(uuid.uuid5(_DEV_IDP_NAMESPACE, normalized))


async def is_dev_idp_available() -> bool:
    """Whether the dev IDP login path is available on this deployment.

    Available when:
    * ``DEPLOYMENT_MODE == 'self_hosted'`` (never on cloud), AND
    * No real IDP is configured in ``oauth_providers`` (no row with
      ``is_idp = True``). Once an admin configures a real IDP, the dev
      IDP is disabled.
    """
    if DEPLOYMENT_MODE != 'self_hosted':
        return False
    idp_providers = await OAuthProviderStore().get_idp_providers()
    return len(idp_providers) == 0


async def get_dev_idp_if_available() -> DevIdpProvider | None:
    """Return the dev IDP sentinel if available, else ``None``.

    Used by ``OAuthProviderStore.get_first_idp()`` and ``get_idp_providers()``
    to make the dev IDP appear as a regular IDP when no real one is configured.
    """
    if await is_dev_idp_available():
        return DevIdpProvider()
    return None


def is_dev_idp_provider_id(provider_id: int) -> bool:
    """Whether ``provider_id`` refers to the dev IDP sentinel."""
    return provider_id == DEV_IDP_PROVIDER_ID


# ── dev IDP login form (served as HTML so it works without frontend changes) ─


_LOGIN_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenHands — Development Login</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1a1a1a; color: #fff; display: flex; align-items: center;
            justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #262626; border-radius: 12px; padding: 40px;
             max-width: 400px; width: 100%; box-sizing: border-box; }}
    h1 {{ font-size: 24px; font-weight: 500; margin: 0 0 8px; }}
    p.desc {{ color: #a3a3a3; font-size: 14px; margin: 0 0 24px; }}
    label {{ display: block; font-size: 14px; color: #a3a3a3; margin-bottom: 6px; }}
    input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 6px;
             border: 1px solid #404040; background: transparent; color: #fff;
             font-size: 14px; outline: none; }}
    input:focus {{ border-color: #6366f1; }}
    button {{ width: 100%; margin-top: 20px; padding: 10px; border-radius: 6px;
              border: none; background: #fff; color: #1a1a1a; font-size: 14px;
              font-weight: 500; cursor: pointer; }}
    button:hover {{ opacity: 0.9; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .warning {{ margin-top: 20px; padding: 12px; border-radius: 6px;
                background: rgba(250, 204, 21, 0.1); border: 1px solid rgba(250, 204, 21, 0.3);
                color: #facc15; font-size: 12px; line-height: 1.4; }}
    .error {{ margin-top: 16px; padding: 10px; border-radius: 6px;
              background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3);
              color: #ef4444; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Development Login</h1>
    <p class="desc">Enter your email to sign in. No password required.</p>
    <form method="POST" action="{callback_url}">
      <input type="hidden" name="state" value="{state}">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" placeholder="you@example.com"
             required autofocus>
      <button type="submit">Sign In</button>
    </form>
    <div class="warning">
      Development mode: authentication is not secure. Configure a real
      identity provider for production use.
    </div>
  </div>
</body>
</html>"""


@dev_idp_router.get(f'/{DEV_IDP_PROVIDER_ID}/login')
async def dev_idp_login_form(
    request: Request,
    redirect_url: str = '',
    mode: str = 'login',
    state: str = '',
):
    """Serve the dev IDP email-entry HTML form.

    This route intercepts ``/oauth/{DEV_IDP_PROVIDER_ID}/login`` (which the
    OAuth v2 ``/oauth/idp-login`` redirect targets) and serves a self-contained
    HTML page with an email input. The form POSTs to the callback endpoint.

    Returns ``404`` if the dev IDP is not available (real IDP configured or
    cloud deployment).
    """
    if not await is_dev_idp_available():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Development IDP is not available',
        )

    # Build the callback URL, preserving redirect_url and mode.
    web_url = get_web_url(request)
    callback_url = f'{web_url}/oauth/{DEV_IDP_PROVIDER_ID}/callback'
    params: dict[str, str] = {}
    if redirect_url:
        params['redirect_url'] = redirect_url
    if mode and mode != 'login':
        params['mode'] = mode
    if params:
        callback_url = f'{callback_url}?{urlencode(params)}'

    # Pass through any state from the idp-login redirect.
    state_value = state or ''

    html = _LOGIN_HTML_TEMPLATE.format(
        callback_url=callback_url,
        state=state_value,
    )
    return HTMLResponse(content=html)


# ── dev IDP callback (completes login, mirrors the OAuth v2 callback) ──────


@dev_idp_router.post(f'/{DEV_IDP_PROVIDER_ID}/callback')
async def dev_idp_callback(
    request: Request,
    email: EmailStr = Form(...),
    state: str = Form(''),
    redirect_url: str = '',
    mode: str = 'login',
):
    """Complete the dev IDP login.

    Receives the email from the HTML form, derives a deterministic user id,
    resolves or creates the ``User`` row, auto-accepts TOS (dev mode), and
    sets the ``openhands_auth`` JWT cookie — the same cookie used by the real
    OAuth v2 callback. Redirects to the app (or onboarding/TOS page).

    Returns ``404`` if the dev IDP is not available.
    """
    if not await is_dev_idp_available():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Development IDP is not available',
        )

    email_str = str(email).strip().lower()
    user_id = derive_dev_idp_user_id(email_str)

    # Try to resolve an existing user — first by the derived id, then by
    # email. The email fallback handles users who were created by a real
    # IDP (their user id is the external IDP's sub, not our hash) but
    # are now logging in through the dev IDP.
    user = await UserStore.get_user_by_id(user_id)
    if user is None:
        user_by_email = await UserStore.get_user_by_email(email_str)
        if user_by_email is not None:
            user_id = str(user_by_email.id)
            user = user_by_email

    is_new_user = user is None
    if is_new_user:
        user_info = {
            'email': email_str,
            'email_verified': True,
            'preferred_username': email_str,
        }
        created = await UserStore.create_user(user_id, user_info)
        if created is None:
            logger.error('dev_idp:failed_to_create_user', extra={'email': email_str})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to create user',
            )
        user = created
        user_id = str(user.id)

    assert user is not None  # narrowed: either existing or just created

    # Auto-accept TOS in dev mode — the dev IDP is for trial/dev only and
    # requiring TOS acceptance adds friction without security value.
    has_accepted_tos = user.accepted_tos is not None
    if not has_accepted_tos:
        await _accept_tos_for_dev_user(user_id)
        has_accepted_tos = True

    await UserStore.record_login(user_id)

    # Apply default org bootstrap for new users (mirrors the Keycloak callback).
    if is_new_user:
        try:
            user = await DefaultOrgBootstrapService.apply_for_user(
                user, is_new_user=True
            )
        except Exception:
            logger.exception(
                'dev_idp:default_org_bootstrap_failed',
                extra={'user_id': user_id},
                stack_info=True,
            )

    # Best-effort analytics identify — never block login on analytics.
    try:
        await _track_dev_idp_login(user_id, email_str)
    except Exception:
        logger.exception('dev_idp:analytics_failed', stack_info=True)

    web_url = get_web_url(request)
    final_redirect_url = redirect_url or '/'

    # Check onboarding redirect (self-hosted: only the first owner/super-admin).
    should_onboard = await _should_redirect_to_onboarding_dev(user_id, user)
    if should_onboard:
        from server.routes.auth import _build_onboarding_redirect

        final_redirect_url = _build_onboarding_redirect(final_redirect_url, web_url)
    else:
        from server.routes.auth import _build_cross_app_redirect_url

        final_redirect_url = _build_cross_app_redirect_url(final_redirect_url, web_url)

    response = RedirectResponse(final_redirect_url, status_code=302)

    _set_dev_idp_cookie(
        request=request,
        response=response,
        user_id=user_id,
        accepted_tos=has_accepted_tos,
        secure=web_url.startswith('https'),
    )

    logger.info(
        'dev_idp:user_logged_in',
        extra={'user_id': user_id, 'is_new_user': is_new_user},
    )
    return response


# Also support GET on the callback for convenience (e.g. direct testing).
@dev_idp_router.get(f'/{DEV_IDP_PROVIDER_ID}/callback')
async def dev_idp_callback_get(
    request: Request,
    email: str = '',
    redirect_url: str = '',
    mode: str = 'login',
):
    """GET variant of the dev IDP callback for direct testing.

    Allows ``GET /oauth/-1/callback?email=dev@example.com`` for quick
    browser-based testing without rendering the form.
    """
    if not email:
        # Redirect to the login form.
        web_url = get_web_url(request)
        form_url = f'{web_url}/oauth/{DEV_IDP_PROVIDER_ID}/login'
        params: dict[str, str] = {}
        if redirect_url:
            params['redirect_url'] = redirect_url
        if params:
            form_url = f'{form_url}?{urlencode(params)}'
        return RedirectResponse(form_url, status_code=302)

    # Reuse the POST handler logic by calling it directly.
    from pydantic import EmailStr as _EmailStr

    # Validate email format.
    try:
        validated = _EmailStr(email)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid email format',
        )

    return await dev_idp_callback(
        request=request,
        email=validated,
        state='',
        redirect_url=redirect_url,
        mode=mode,
    )


# ── status endpoint (for config injection) ─────────────────────────────────


def _dev_idp_status_router() -> APIRouter:
    """Create a separate router for the status endpoint at /api/dev-idp/status."""
    router = APIRouter(prefix='/api/dev-idp', tags=['Dev IDP'])

    @router.get('/status')
    async def dev_idp_status() -> JSONResponse:
        enabled = await is_dev_idp_available()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'enabled': enabled},
        )

    return router


dev_idp_status_router = _dev_idp_status_router()


# ── helpers ────────────────────────────────────────────────────────────────


def _set_dev_idp_cookie(
    request: Request,
    response: RedirectResponse,
    user_id: str,
    accepted_tos: bool,
    secure: bool,
) -> None:
    """Set the ``openhands_auth`` JWT cookie for a dev IDP session.

    Mirrors ``_set_oauth_v2_cookie`` from ``oauth_v2.py`` but with no IDP
    token expiry (the dev IDP has no external token to refresh). The cookie
    carries only ``user_id``, ``accepted_tos``, and ``iat``.
    """
    from server.auth.oauth_v2_refresh import COOKIE_MAX_AGE_CAP_SECONDS

    max_age_seconds = COOKIE_MAX_AGE_CAP_SECONDS
    payload = create_oauth_v2_cookie_payload(
        user_id,
        access_token_expires_at=None,
        accepted_tos=accepted_tos,
    )
    signed = sign_oauth_v2_cookie(payload, max_age_seconds)
    response.set_cookie(
        key='openhands_auth',
        value=signed,
        max_age=max_age_seconds,
        domain=get_cookie_domain(),
        secure=secure,
        httponly=True,
        samesite=get_cookie_samesite(),
    )


async def _accept_tos_for_dev_user(user_id: str) -> None:
    """Auto-accept TOS for a dev IDP user (dev mode only)."""
    from sqlalchemy import select

    from storage.database import a_session_maker
    from storage.user import User

    accepted_tos = datetime.now(timezone.utc).replace(tzinfo=None)
    async with a_session_maker() as session:
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        user = result.scalar_one_or_none()
        if user is None:
            return
        user.accepted_tos = accepted_tos
        user.user_consents_to_analytics = True
        await session.commit()


async def _track_dev_idp_login(user_id: str, email: str) -> None:
    """Best-effort analytics identify for dev IDP login."""
    from openhands.analytics import get_analytics_service

    analytics = get_analytics_service()
    if not analytics:
        return

    from openhands.analytics.analytics_context import AnalyticsContext

    ctx = AnalyticsContext(
        user_id=user_id,
        consented=True,
        org_id=None,
        user=None,
    )
    analytics.identify_user(
        ctx=ctx,
        email=email,
        org_name=None,
        idp='dev_idp',
        orgs=[],
    )
    analytics.track_user_logged_in(ctx=ctx, idp='dev_idp')


async def _should_redirect_to_onboarding_dev(user_id: str, user) -> bool:
    """Check onboarding redirect for dev IDP users.

    Delegates to the shared ``_should_redirect_to_onboarding`` logic from
    the auth routes so behavior is consistent with the Keycloak/OAuth path.
    """
    from server.routes.auth import _should_redirect_to_onboarding

    return await _should_redirect_to_onboarding(user_id, user)
