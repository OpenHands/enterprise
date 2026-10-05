"""Development-only insecure IDP — pretends to be a regular OAuth v2 IDP.

This module provides a login path that does **not** depend on Keycloak or any
external identity provider. It is intended for self-hosted / trial installs
that have no real IDP configured yet.

**Design**: the dev IDP is a *real* row in ``oauth_providers``
(``provider_category = 'dev_idp'``, ``is_idp = True``), seeded by migration
175 — not a synthetic/in-memory object. It plugs into the existing OAuth v2
flow like any other IDP:

* ``OAuthProviderStore.get_first_idp()`` / ``get_idp_providers()`` include the
  dev IDP row only when it is *active* — ``DEPLOYMENT_MODE == 'self_hosted'``
  and no other real IDP is configured (``_has_real_idp()`` excludes the dev
  IDP's own row, so configuring a real IDP later disables it).
* ``GET /oauth/idp-login`` redirects to ``/oauth/{id}/login``, where ``id`` is
  the dev IDP row's real (positive) database id. ``GET /oauth/{id}/login``
  (in ``server.routes.oauth_v2``) recognizes the row by
  ``provider_category == DEV_IDP_CATEGORY`` and redirects to
  ``/oauth/dev-idp/login`` — a dedicated, fixed path (not keyed by the row's
  id, which depends on insert order and isn't worth hardcoding) served by
  this module.
* Unlike a real IDP, the dev IDP requires a **password**: ``GET
  /oauth/dev-idp/login`` and ``GET /oauth/dev-idp/signup`` serve HTML
  email+password forms; the corresponding ``POST`` routes verify credentials
  (sign-in) or create an account (sign-up) and set the same ``openhands_auth``
  JWT cookie the real OAuth v2 callback uses. Passwords are hashed with
  Argon2id (``server.auth.password_hashing``) and stored in
  ``User.password_hash`` — never sent anywhere but this process, and never
  accepted via query string/GET.

When the dev IDP is not active (real IDP configured, or cloud deployment),
every route in this module returns ``404``.

**This IDP is intentionally insecure** (no rate limiting, no email
verification, no password-reset flow). It must never be enabled on cloud
(``app.all-hands.dev``) or any deployment where security matters.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.oauth_v2_refresh import (
    create_oauth_v2_cookie_payload,
    sign_oauth_v2_cookie,
)
from server.auth.password_hashing import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
from server.constants import DEPLOYMENT_MODE
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.default_org_service import DefaultOrgBootstrapService
from storage.oauth_provider import DEV_IDP_CATEGORY
from storage.oauth_provider_store import OAuthProviderStore
from storage.user import User
from storage.user_store import UserStore

dev_idp_router = APIRouter(prefix='/oauth', tags=['Dev IDP'])

# Fixed path segments for the dev IDP's own login/signup pages — not keyed by
# the seeded row's database id (which depends on insert order and isn't worth
# hardcoding). ``server.routes.oauth_v2`` redirects here once it resolves a
# provider row to have ``provider_category == DEV_IDP_CATEGORY``.
DEV_IDP_LOGIN_PATH = 'dev-idp/login'
DEV_IDP_SIGNUP_PATH = 'dev-idp/signup'

# Fixed namespace for deterministic user-id derivation from email. Required
# by ``UserStore.create_user``'s identity-preservation contract: ``User.id``
# must be stable across calls for the same external identity (there is no
# Keycloak ``sub`` for the dev IDP, so the email itself fills that role).
_DEV_IDP_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890')


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
    * No *other* real IDP is configured in ``oauth_providers`` — the dev
      IDP's own seeded row is excluded from ``_has_real_idp()``. Once an
      admin configures a real IDP, the dev IDP is disabled even though its
      row remains in the table.
    """
    if DEPLOYMENT_MODE != 'self_hosted':
        return False
    return not await OAuthProviderStore()._has_real_idp()


def is_dev_idp_provider(provider) -> bool:
    """Whether ``provider`` (an ``OAuthProvider`` row) is the dev IDP."""
    return provider.provider_category == DEV_IDP_CATEGORY


# ── dev IDP login / sign-up forms (served as HTML, no frontend changes) ────


_ERROR_MESSAGES = {
    'invalid_credentials': 'Invalid email or password.',
    'email_taken': (
        'An account with that email already exists. Try signing in instead.'
    ),
    'password_too_short': (
        f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'
    ),
    'password_mismatch': 'Passwords do not match.',
}

_FORM_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenHands — Development {mode_title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1a1a1a; color: #fff; display: flex; align-items: center;
            justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #262626; border-radius: 12px; padding: 40px;
             max-width: 400px; width: 100%; box-sizing: border-box; }}
    h1 {{ font-size: 24px; font-weight: 500; margin: 0 0 8px; }}
    p.desc {{ color: #a3a3a3; font-size: 14px; margin: 0 0 24px; }}
    label {{ display: block; font-size: 14px; color: #a3a3a3; margin-bottom: 6px;
             margin-top: 16px; }}
    label:first-of-type {{ margin-top: 0; }}
    input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 6px;
             border: 1px solid #404040; background: transparent; color: #fff;
             font-size: 14px; outline: none; }}
    input:focus {{ border-color: #6366f1; }}
    button {{ width: 100%; margin-top: 20px; padding: 10px; border-radius: 6px;
              border: none; background: #fff; color: #1a1a1a; font-size: 14px;
              font-weight: 500; cursor: pointer; }}
    button:hover {{ opacity: 0.9; }}
    .toggle {{ margin-top: 16px; font-size: 13px; color: #a3a3a3; text-align: center; }}
    .toggle a {{ color: #818cf8; text-decoration: none; }}
    .warning {{ margin-top: 20px; padding: 12px; border-radius: 6px;
                background: rgba(250, 204, 21, 0.1); border: 1px solid rgba(250, 204, 21, 0.3);
                color: #facc15; font-size: 12px; line-height: 1.4; }}
    .error {{ margin-top: 0; margin-bottom: 16px; padding: 10px; border-radius: 6px;
              background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3);
              color: #ef4444; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Development {mode_title}</h1>
    <p class="desc">{description}</p>
    {error_html}
    <form method="POST" action="{form_action}">
      <input type="hidden" name="redirect_url" value="{redirect_url}">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" placeholder="you@example.com"
             value="{email}" required autofocus>
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required
             minlength="{min_password_length}">
      {confirm_password_html}
      <button type="submit">{submit_label}</button>
    </form>
    <div class="toggle">{toggle_html}</div>
    <div class="warning">
      Development mode: this account is not secure. Configure a real
      identity provider for production use.
    </div>
  </div>
</body>
</html>"""

_CONFIRM_PASSWORD_HTML = """      <label for="confirm_password">Confirm password</label>
      <input type="password" id="confirm_password" name="confirm_password" required
             minlength="{min_password_length}">
"""


def _render_form(
    *,
    mode: str,
    web_url: str,
    redirect_url: str,
    error: str = '',
    email: str = '',
) -> str:
    """Render the login or sign-up HTML form.

    ``mode`` is ``'login'`` or ``'signup'`` — selects the form action, the
    confirm-password field, and the toggle link to the other mode.
    """
    is_signup = mode == 'signup'
    form_action = (
        f'{web_url}/oauth/{DEV_IDP_SIGNUP_PATH if is_signup else DEV_IDP_LOGIN_PATH}'
    )
    toggle_target = DEV_IDP_LOGIN_PATH if is_signup else DEV_IDP_SIGNUP_PATH
    toggle_params = urlencode({'redirect_url': redirect_url}) if redirect_url else ''
    toggle_url = f'{web_url}/oauth/{toggle_target}'
    if toggle_params:
        toggle_url = f'{toggle_url}?{toggle_params}'
    toggle_html = (
        f'Already have an account? <a href="{toggle_url}">Sign in</a>'
        if is_signup
        else f'Need an account? <a href="{toggle_url}">Sign up</a>'
    )
    error_html = ''
    if error:
        message = _ERROR_MESSAGES.get(error, 'Something went wrong. Please try again.')
        error_html = f'<div class="error">{message}</div>'

    return _FORM_HTML_TEMPLATE.format(
        mode_title='Sign Up' if is_signup else 'Login',
        description=(
            'Create a development account. No email verification required.'
            if is_signup
            else 'Sign in with your development account.'
        ),
        error_html=error_html,
        form_action=form_action,
        redirect_url=redirect_url,
        email=email,
        min_password_length=MIN_PASSWORD_LENGTH,
        confirm_password_html=(
            _CONFIRM_PASSWORD_HTML.format(min_password_length=MIN_PASSWORD_LENGTH)
            if is_signup
            else ''
        ),
        submit_label='Create account' if is_signup else 'Sign in',
        toggle_html=toggle_html,
    )


async def _require_dev_idp_available() -> None:
    if not await is_dev_idp_available():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Development IDP is not available',
        )


@dev_idp_router.get(f'/{DEV_IDP_LOGIN_PATH}')
async def dev_idp_login_form(
    request: Request,
    redirect_url: str = '',
    error: str = '',
):
    """Serve the dev IDP email+password login form.

    Returns ``404`` if the dev IDP is not available (real IDP configured or
    cloud deployment).
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    html = _render_form(
        mode='login', web_url=web_url, redirect_url=redirect_url, error=error
    )
    return HTMLResponse(content=html)


@dev_idp_router.get(f'/{DEV_IDP_SIGNUP_PATH}')
async def dev_idp_signup_form(
    request: Request,
    redirect_url: str = '',
    error: str = '',
):
    """Serve the dev IDP email+password sign-up form.

    Returns ``404`` if the dev IDP is not available (real IDP configured or
    cloud deployment).
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    html = _render_form(
        mode='signup', web_url=web_url, redirect_url=redirect_url, error=error
    )
    return HTMLResponse(content=html)


def _form_redirect(
    web_url: str, *, mode: str, error: str, redirect_url: str
) -> RedirectResponse:
    """Redirect back to the login/sign-up form with an error message."""
    path = DEV_IDP_SIGNUP_PATH if mode == 'signup' else DEV_IDP_LOGIN_PATH
    params = {'error': error}
    if redirect_url:
        params['redirect_url'] = redirect_url
    target = f'{web_url}/oauth/{path}?{urlencode(params)}'
    return RedirectResponse(target, status_code=302)


@dev_idp_router.post(f'/{DEV_IDP_LOGIN_PATH}')
async def dev_idp_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    redirect_url: str = Form(''),
):
    """Verify email + password and complete the dev IDP login.

    Returns ``404`` if the dev IDP is not available. On invalid credentials,
    redirects back to the login form with an error instead of failing the
    request outright — there is nothing sensitive to protect by
    distinguishing "no such account" from "wrong password" here.
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    email_str = email.strip().lower()

    user = await UserStore.get_user_by_id(derive_dev_idp_user_id(email_str))
    if user is None:
        user = await UserStore.get_user_by_email(email_str)

    if (
        user is None
        or not user.password_hash
        or not verify_password(password, user.password_hash)
    ):
        logger.info('dev_idp:login_failed', extra={'email': email_str})
        return _form_redirect(
            web_url,
            mode='login',
            error='invalid_credentials',
            redirect_url=redirect_url,
        )

    return await _complete_dev_idp_login(
        request=request,
        user=user,
        is_new_user=False,
        email=email_str,
        redirect_url=redirect_url,
    )


@dev_idp_router.post(f'/{DEV_IDP_SIGNUP_PATH}')
async def dev_idp_signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    redirect_url: str = Form(''),
):
    """Create a dev IDP account (email + password) and complete the login.

    Returns ``404`` if the dev IDP is not available. Redirects back to the
    sign-up form with an error on a taken email, a too-short password, or a
    confirm-password mismatch.
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    email_str = email.strip().lower()

    if password != confirm_password:
        return _form_redirect(
            web_url,
            mode='signup',
            error='password_mismatch',
            redirect_url=redirect_url,
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return _form_redirect(
            web_url,
            mode='signup',
            error='password_too_short',
            redirect_url=redirect_url,
        )

    user_id = derive_dev_idp_user_id(email_str)
    existing = await UserStore.get_user_by_id(user_id)
    if existing is None:
        existing = await UserStore.get_user_by_email(email_str)
    if existing is not None:
        return _form_redirect(
            web_url,
            mode='signup',
            error='email_taken',
            redirect_url=redirect_url,
        )

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

    await _set_password_hash(user_id, hash_password(password))

    return await _complete_dev_idp_login(
        request=request,
        user=created,
        is_new_user=True,
        email=email_str,
        redirect_url=redirect_url,
    )


# ── shared post-authentication steps (mirrors the real OAuth v2 callback) ──


async def _complete_dev_idp_login(
    *,
    request: Request,
    user: User,
    is_new_user: bool,
    email: str,
    redirect_url: str,
) -> RedirectResponse:
    """Finish a successful dev IDP login/sign-up: TOS, org, analytics, cookie.

    Shared by ``dev_idp_login`` (existing account) and ``dev_idp_signup``
    (brand-new account) so both end up with identical post-auth behavior.
    """
    user_id = str(user.id)

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
        await _track_dev_idp_login(user_id, email)
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


async def _set_password_hash(user_id: str, password_hash: str) -> None:
    """Persist the Argon2id hash of a dev IDP account's password."""
    from sqlalchemy import select

    from storage.database import a_session_maker

    async with a_session_maker() as session:
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        user = result.scalar_one_or_none()
        if user is None:
            return
        user.password_hash = password_hash
        await session.commit()


async def _accept_tos_for_dev_user(user_id: str) -> None:
    """Auto-accept TOS for a dev IDP user (dev mode only)."""
    from sqlalchemy import select

    from storage.database import a_session_maker

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
