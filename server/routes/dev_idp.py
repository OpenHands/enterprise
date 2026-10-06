"""Local password-based IDP — pretends to be a regular OAuth v2 IDP.

This module provides a login path that does **not** depend on Keycloak or any
external identity provider. It is intended for self-hosted installs that have
no real IDP configured yet, with their first administrator account bootstrapped
directly, instead of standing up an external IDP just to get started.

**Design**: this IDP plugs into the existing OAuth v2 flow as if it were a
regular IDP provider, but is modeled as an in-memory sentinel
(``DevIdpProvider``, id = ``DEV_IDP_PROVIDER_ID``) rather than a row in
``oauth_providers``. When no real IDP is configured and the deployment is
self-hosted:

* ``OAuthProviderStore.get_first_idp()`` returns the ``DevIdpProvider``
  sentinel.
* ``GET /oauth/idp-login`` redirects to ``/oauth/{DEV_IDP_PROVIDER_ID}/login``,
  which ``oauth_v2`` intercepts and redirects to the fixed
  ``/oauth/dev-idp/login`` page served by this module.
* Unlike a real IDP, this one requires a **password**: ``GET
  /oauth/dev-idp/login`` and ``GET /oauth/dev-idp/signup`` serve HTML
  email+password forms; the corresponding ``POST`` routes verify credentials
  (sign-in) or create an account (sign-up) and set the same ``openhands_auth``
  JWT cookie the real OAuth v2 callback uses. Passwords are hashed with
  Argon2id (``server.auth.password_hashing``) and stored in
  ``User.password_hash`` — never sent anywhere but this process, and never
  accepted via query string/GET.

**Account creation is admin-only, not self-service.** The sign-up form exists
solely to bootstrap the *first* super admin on a fresh installation
(``UserStore.create_user`` already designates the first user in an empty
database as super admin) — or to finish bootstrapping one who already exists
but has no password yet (e.g. a super admin backfilled by migration 138 on
an installation that predates ``User.password_hash``, or one who has only
ever signed in through a real IDP). The gate is
``UserStore.has_super_admin_with_password()``, not
``UserStore.has_super_admin()``: a super admin *row* existing is not enough
to hide this form — only a super admin who can actually log in with a
password is. Once that's true, ``/oauth/dev-idp/signup`` redirects to the
login page instead of rendering — every subsequent account must be created
by a super admin through the existing user-management APIs, not through
self-service sign-up. Symmetrically, while no super admin has a password
yet, ``/oauth/dev-idp/login`` redirects to the sign-up (bootstrap) page,
since there is no account that can log in. Submitting sign-up updates the
password on a matching existing user (found by derived id, then by email —
e.g. the passwordless super admin itself) instead of creating a new one;
only a genuinely new email creates a brand-new account.

When a real IDP is configured, the sentinel is not returned and every route
in this module returns ``404``.

**This IDP is not a substitute for a real identity provider** (no rate
limiting, no email verification, no password-reset flow, no MFA). It must
never be enabled on cloud (``app.all-hands.dev``) or any deployment where
security matters. Gated by the ``INTEGRATED_IDP_ENABLED`` env var (explicit opt-in)
plus the "no real IDP configured" check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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
from server.constants import INTEGRATED_IDP_ENABLED
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.default_org_service import DefaultOrgBootstrapService
from storage.oauth_provider_store import OAuthProviderStore
from storage.user import User
from storage.user_store import UserStore

dev_idp_router = APIRouter(prefix='/oauth', tags=['Dev IDP'])

# Sentinel provider ID used by the dev IDP.  Negative so it can never collide
# with a real DB row (Identity columns start at 1).
DEV_IDP_PROVIDER_ID = -1
DEV_IDP_CATEGORY = 'dev_idp'

# Fixed path segments for the dev IDP's own login/signup pages — not keyed by
# the sentinel provider id. ``server.routes.oauth_v2`` redirects here once it
# intercepts ``provider_id == DEV_IDP_PROVIDER_ID``.
DEV_IDP_LOGIN_PATH = 'dev-idp/login'
DEV_IDP_SIGNUP_PATH = 'dev-idp/signup'

# Fixed namespace for deterministic user-id derivation from email. Required
# by ``UserStore.create_user``'s identity-preservation contract: ``User.id``
# must be stable across calls for the same external identity (there is no
# Keycloak ``sub`` for the dev IDP, so the email itself fills that role).
_DEV_IDP_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890')


@dataclass(frozen=True)
class DevIdpProvider:
    """Sentinel that quacks like ``OAuthProvider`` for the OAuth v2 flow.

    Returned by ``OAuthProviderStore.get_first_idp()`` when no real IDP is
    configured on a self-hosted deployment. The OAuth v2 routes check
    ``provider.id == DEV_IDP_PROVIDER_ID`` to intercept and redirect to the
    dev IDP email+password form instead of building an external OAuth URL.
    """

    id: int = DEV_IDP_PROVIDER_ID
    provider_category: str = DEV_IDP_CATEGORY
    display_name: str = 'Password Login'
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
    * ``INTEGRATED_IDP_ENABLED`` is set (explicit opt-in via env var), AND
    * No real IDP is configured in ``oauth_providers`` (no row with
      ``is_idp = True``). Once an admin configures a real IDP, the dev
      IDP is disabled.

    Uses ``_has_real_idp()`` (direct DB query) instead of
    ``get_idp_providers()`` to avoid infinite recursion: ``get_idp_providers``
    calls ``get_dev_idp_if_available`` → ``is_dev_idp_available``.
    """
    if not INTEGRATED_IDP_ENABLED:
        return False
    return not await OAuthProviderStore()._has_real_idp()


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
    'superadmin_exists': (
        'An administrator account already exists. Please sign in instead.'
    ),
}

_FORM_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenHands — {mode_title}</title>
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
    .error {{ margin-top: 0; margin-bottom: 16px; padding: 10px; border-radius: 6px;
              background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3);
              color: #ef4444; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{mode_title}</h1>
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
    """Render the login or admin-account-creation HTML form.

    ``mode`` is ``'login'`` or ``'signup'`` — selects the form action, the
    title/description, and whether a confirm-password field is shown.
    Neither form links to the other: while no super admin exists, sign-up
    (account bootstrap) is the only option; once one exists, login is the
    only option (see the route handlers for the redirect logic).
    """
    is_signup = mode == 'signup'
    form_action = (
        f'{web_url}/oauth/{DEV_IDP_SIGNUP_PATH if is_signup else DEV_IDP_LOGIN_PATH}'
    )
    error_html = ''
    if error:
        message = _ERROR_MESSAGES.get(error, 'Something went wrong. Please try again.')
        error_html = f'<div class="error">{message}</div>'

    return _FORM_HTML_TEMPLATE.format(
        mode_title='Create Admin Account' if is_signup else 'Sign In',
        description=(
            'Create the first administrator account for this installation.'
            if is_signup
            else 'Sign in to your OpenHands account.'
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
        submit_label='Create admin account' if is_signup else 'Sign in',
    )


async def _require_dev_idp_available() -> None:
    if not await is_dev_idp_available():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Password login is not available',
        )


def _mode_redirect(web_url: str, *, mode: str, redirect_url: str) -> RedirectResponse:
    """Redirect to the login/sign-up form, with no error, preserving ``redirect_url``.

    Used to steer the caller to whichever of the two pages is currently the
    "only option" (see module docstring): sign-up while no super admin
    exists yet, login once one does.
    """
    path = DEV_IDP_SIGNUP_PATH if mode == 'signup' else DEV_IDP_LOGIN_PATH
    target = f'{web_url}/oauth/{path}'
    if redirect_url:
        target = f'{target}?{urlencode({"redirect_url": redirect_url})}'
    return RedirectResponse(target, status_code=302)


@dev_idp_router.get(f'/{DEV_IDP_LOGIN_PATH}')
async def dev_idp_login_form(
    request: Request,
    redirect_url: str = '',
    error: str = '',
):
    """Serve the email+password login form.

    Returns ``404`` if this IDP is not available (real IDP configured or
    cloud deployment). Redirects to the sign-up (bootstrap) page if no super
    admin can log in with a password yet — there is nothing to log into
    until one is created (or an existing passwordless super admin claims
    their account).
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    if not await UserStore.has_super_admin_with_password():
        return _mode_redirect(web_url, mode='signup', redirect_url=redirect_url)
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
    """Serve the email+password admin-account-creation form.

    Returns ``404`` if this IDP is not available (real IDP configured or
    cloud deployment). Redirects to the login page once a super admin can
    already log in with a password — self-service account creation is
    bootstrap-only; every subsequent account is created by a super admin,
    not through this form. A super admin *row* existing with no password set
    yet (e.g. backfilled before ``User.password_hash`` existed) does **not**
    hide this form — it is still needed to finish that super admin's
    bootstrap.
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    if await UserStore.has_super_admin_with_password():
        return _mode_redirect(web_url, mode='login', redirect_url=redirect_url)
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
    """Verify email + password and complete the login.

    Returns ``404`` if this IDP is not available. Redirects to the sign-up
    (bootstrap) page if no super admin can log in with a password yet. On
    invalid credentials, redirects back to the login form with an error
    instead of failing the request outright — there is nothing sensitive to
    protect by distinguishing "no such account" from "wrong password" here.
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    if not await UserStore.has_super_admin_with_password():
        return _mode_redirect(web_url, mode='signup', redirect_url=redirect_url)
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
    """Create or finish bootstrapping the super admin (email + password).

    Returns ``404`` if this IDP is not available. This endpoint only ever
    succeeds while **no super admin can yet log in with a password** — it is
    a one-time bootstrap step, not general self-service sign-up. If a super
    admin with a password already exists (including one created by a
    request that raced this one — re-checked here, not just by the GET
    form), redirects to the login form instead; every subsequent account
    must be created by a super admin through the existing user-management
    APIs. If the email belongs to an existing user with no ``password_hash``
    — an OAuth-provisioned account, or a super admin backfilled before
    ``User.password_hash`` existed (migration 138) — sign-up sets the
    password on *that* user and claims the account rather than creating a
    new one, granting it the super-admin role too if it doesn't already hold
    it. Redirects back to the sign-up form with an error on a taken email
    (password already set), a too-short password, or a confirm-password
    mismatch.
    """
    await _require_dev_idp_available()
    web_url = get_web_url(request)
    if await UserStore.has_super_admin_with_password():
        return _form_redirect(
            web_url,
            mode='login',
            error='superadmin_exists',
            redirect_url=redirect_url,
        )
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

    # If the user already has a password, the account is claimed — don't
    # allow overwriting it via sign-up (would be a password-reset bypass).
    # If the user exists but password_hash is NULL (e.g. created via OAuth
    # or provisioned without a password), allow sign-up to set one.
    if existing is not None and existing.password_hash is not None:
        return _form_redirect(
            web_url,
            mode='signup',
            error='email_taken',
            redirect_url=redirect_url,
        )

    hashed = hash_password(password)

    user: User | None
    if existing is not None:
        # Claim an existing passwordless account. This form only ever
        # succeeds while no super admin can log in with a password yet
        # (checked above), so whoever completes it is bootstrapping *the*
        # super admin — grant the role too if the claimed account doesn't
        # already hold it (e.g. a plain OAuth-provisioned account being
        # claimed as the first admin, as opposed to a super admin from
        # migration 138's backfill who already holds it and just needs a
        # password). Idempotent, so always safe to call.
        await _set_password_hash(str(existing.id), hashed)
        user = await UserStore.grant_super_admin(str(existing.id))
        if user is None:
            logger.error(
                'dev_idp:signup_claim_grant_failed', extra={'email': email_str}
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to grant super-admin role',
            )
        is_new_user = False
    else:
        # Create a brand-new account.
        user_info = {
            'email': email_str,
            'email_verified': True,
            'preferred_username': email_str,
        }
        user = await UserStore.create_user(user_id, user_info)
        if user is None:
            logger.error('dev_idp:failed_to_create_user', extra={'email': email_str})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to create user',
            )
        await _set_password_hash(str(user.id), hashed)
        is_new_user = True

    return await _complete_dev_idp_login(
        request=request,
        user=user,
        is_new_user=is_new_user,
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
