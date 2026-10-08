"""Local password-based IDP — pretends to be a regular OAuth v2 IDP.

This module provides a login path that does **not** depend on Keycloak or any
external identity provider. It is intended for self-hosted installs that have
no real IDP configured yet, with their first administrator account bootstrapped
directly, instead of standing up an external IDP just to get started.

**Design**: this IDP plugs into the existing OAuth v2 flow as if it were a
regular IDP provider, and (since migration 179) *is* one: a real row in
``oauth_providers`` (``provider_category=INTEGRATED_IDP_CATEGORY``) seeded
once from the ``ENABLE_INTEGRATED_IDP`` env var, rather than an in-memory
sentinel re-checked on every request. This removes the need to special-case
it anywhere: whether it is "available" is simply whether that row exists
(``_get_integrated_idp_provider``), and every session authenticated through
it carries the row's real id wherever a provider id is needed, including the
``openhands_auth`` cookie — the same as a real external IDP. Subsequent
enable/disable is expected to go through a future ``oauth_providers`` CRUD
API, not this env var (which is only read by the migration).

* ``OAuthProviderStore.get_first_idp()`` picks whichever ``is_idp`` row was
  created most recently — no special-casing of the integrated IDP vs. a
  configured real IDP.
* ``GET /oauth/idp-login`` redirects to ``/oauth/{id}/login`` for that row,
  which ``oauth_v2`` intercepts (by ``provider_category``, not by id) and
  redirects to the fixed ``/oauth/idp/login`` page served by this module.
* Unlike a real IDP, this one requires a **password**: ``GET
  /oauth/idp/login`` and ``GET /oauth/idp/signup`` serve HTML
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
password is. Once that's true, ``/oauth/idp/signup`` redirects to the
login page instead of rendering — every subsequent account must be created
by a super admin through the existing user-management APIs, not through
self-service sign-up. Symmetrically, while no super admin has a password
yet, ``/oauth/idp/login`` redirects to the sign-up (bootstrap) page,
since there is no account that can log in. Submitting sign-up updates the
password on a matching existing user (found by derived id, then by email —
e.g. the passwordless super admin itself) instead of creating a new one;
only a genuinely new email creates a brand-new account.

**Admin-issued sign-up / password-reset links (OHE-3510).** Once the
bootstrap super admin exists, every *subsequent* account is created
out-of-band, and any account's password can be reset the same way: a super
admin calls ``POST /api/idp/signup-links`` (gated by the instance-level
``CREATE_SIGNUP_LINK`` permission) to mint a link naming a target email and
a ``role`` to grant, carried as a signed, 72-hour-expiring JWT — no
server-side state, so there is nothing to revoke or clean up, and no
password ever passes through the admin. The recipient follows the link to
``GET``/``POST /oauth/idp/invite``, which verifies the token and lets them
choose their own password before logging in. If the email belongs to an
existing account, this **resets that account's password** (even if it
already had one) rather than refusing — this is the only password-reset
path this IDP has, self-service or otherwise. A genuinely new email
instead creates a brand-new account, exactly as ``/oauth/idp/signup`` does
for the bootstrap super admin. ``role`` is one of the org-scoped roles
(``member`` / ``admin`` / ``owner`` — only meaningful together with an
``org_id``, which the recipient is added to with that role) or the
instance-level ``superadmin`` (never combined with an ``org_id``, since it
grants the same cross-organization ``user.role_id`` the minting caller
itself holds — see ``server.routes.super_admins``). See the "admin-issued
sign-up links" section below for the full design.

When no integrated-IDP row exists (``ENABLE_INTEGRATED_IDP`` was unset or the
deployment is cloud when migration 179 ran), every route in this module
returns ``404``.

**This IDP is not a substitute for a real identity provider** (no rate
limiting, no email verification, no self-service "forgot password" flow —
only the admin-issued link above, no MFA). It must never be enabled on
cloud (``app.all-hands.dev``) or any deployment where security matters —
migration 179 refuses to seed its row there even if the env var is set.
"""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt as pyjwt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, field_validator

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, authorize_permission
from server.auth.oauth_v2_refresh import (
    create_oauth_v2_cookie_payload,
    sign_oauth_v2_cookie,
)
from server.auth.password_hashing import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
from server.constants import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
)
from server.utils.rate_limit_utils import (
    RATE_LIMIT_SET_PASSWORD_IP_SECONDS,
    RATE_LIMIT_SET_PASSWORD_USER_SECONDS,
    check_rate_limit_by_user_id,
)
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.default_org_service import DefaultOrgBootstrapService
from storage.oauth_provider import INTEGRATED_IDP_CATEGORY, OAuthProvider
from storage.oauth_provider_store import OAuthProviderStore
from storage.org_member_store import OrgMemberStore
from storage.org_service import OrgService
from storage.org_store import OrgStore
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import UserStore

idp_router = APIRouter(prefix='/oauth', tags=['IDP'])

# Fixed path segments for the dev IDP's own login/signup pages — not keyed by
# the provider's DB id. ``server.routes.oauth_v2`` redirects here once it
# intercepts a login/callback for the ``INTEGRATED_IDP_CATEGORY`` row. Named
# "idp" rather than "dev-idp" because this is an integrated IDP offered to
# customers who are evaluating the product or have no real IDP of their own,
# not merely a developer-only tool.
IDP_LOGIN_PATH = 'idp/login'
IDP_SIGNUP_PATH = 'idp/signup'
# Super-admin-issued, one-time sign-up link: GET renders a set-password form
# (email pre-filled from the signed token, not user-editable), POST verifies
# the token and claims/creates the account. See "admin-issued sign-up links"
# section below for the full design.
IDP_INVITE_PATH = 'idp/invite'

# How long an admin-issued sign-up link (``IDP_INVITE_PATH``) remains valid.
# Encoded as the JWT's ``exp`` claim by ``_create_signup_link_token`` —
# verification (``JwtService.verify_jws_token``) rejects the token outright
# once this elapses, so there is no separate expiry check to get wrong.
SIGNUP_LINK_EXPIRY_HOURS = 72

# ``purpose`` claim embedded in sign-up link tokens, checked by
# ``_verify_signup_link_token`` so a token minted for some other purpose
# (e.g. a future use of the shared ``JwtService``) can never be replayed
# here as a sign-up link.
_SIGNUP_LINK_PURPOSE = 'idp_signup_link'

# Conceptual ``role`` claim value that grants the instance-level super-admin
# role on accept, rather than adding the recipient to an organization. Not a
# row in ``role`` -- mirrors ``server.auth.authorization.super_role_name``,
# which labels the same ``admin`` role row "superadmin" when referenced via
# ``user.role_id`` instead of ``org_member.role_id``.
_SIGNUP_LINK_ROLE_SUPERADMIN = 'superadmin'

# The org-scoped roles a sign-up link may grant membership as -- real rows
# in the ``role`` table, assignable via ``org_member.role_id``.
_SIGNUP_LINK_ORG_ROLES = frozenset({ROLE_MEMBER, ROLE_ADMIN, ROLE_OWNER})

# Every ``role`` value ``POST /api/idp/signup-links`` accepts.
_SIGNUP_LINK_ROLES = _SIGNUP_LINK_ORG_ROLES | {_SIGNUP_LINK_ROLE_SUPERADMIN}

# Fixed namespace for deterministic user-id derivation from email. Required
# by ``UserStore.create_user``'s identity-preservation contract: ``User.id``
# must be stable across calls for the same external identity (there is no
# Keycloak ``sub`` for the dev IDP, so the email itself fills that role).
_IDP_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890')


def derive_idp_user_id(email: str) -> str:
    """Derive a deterministic UUID from an email address.

    Uses ``uuid.uuid5`` (SHA-1 based) so the same email always maps to the
    same user id. The email is lower-cased and stripped before hashing so
    ``Alice@Example.COM`` and ``alice@example.com`` resolve to the same user.
    """
    normalized = email.strip().lower()
    return str(uuid.uuid5(_IDP_NAMESPACE, normalized))


@dataclass(frozen=True)
class SignupLinkPayload:
    """Decoded contents of a verified admin-issued sign-up link token."""

    email: str
    org_id: uuid.UUID | None
    # One of ``_SIGNUP_LINK_ROLES``: an org-scoped role (meaningful only
    # together with ``org_id``) or ``_SIGNUP_LINK_ROLE_SUPERADMIN`` (always
    # paired with ``org_id is None``).
    role: str


def _create_signup_link_token(
    email: str,
    org_id: uuid.UUID | None = None,
    role: str = ROLE_MEMBER,
) -> str:
    """Sign a JWT for an admin-issued sign-up link.

    Carries the invited ``email``, the ``role`` to grant on accept (an
    org-scoped role alongside ``org_id``, see ``_ensure_org_membership``,
    or ``_SIGNUP_LINK_ROLE_SUPERADMIN`` to grant the instance-level
    super-admin role instead), and a ``purpose`` claim, plus the standard
    ``iat``/``exp`` claims added by ``JwtService.create_jws_token``.
    Expires after ``SIGNUP_LINK_EXPIRY_HOURS`` — enforced by
    ``JwtService.verify_jws_token`` (via the underlying ``jwt.decode``),
    not by any separate check here.
    """
    from storage.encrypt_utils import get_jwt_service

    payload: dict[str, str] = {
        'purpose': _SIGNUP_LINK_PURPOSE,
        'email': email,
        'role': role,
    }
    if org_id is not None:
        payload['org_id'] = str(org_id)
    return get_jwt_service().create_jws_token(
        payload, expires_in=timedelta(hours=SIGNUP_LINK_EXPIRY_HOURS)
    )


def _verify_signup_link_token(token: str) -> SignupLinkPayload:
    """Verify a sign-up link JWT and return its decoded payload.

    Raises ``ValueError`` uniformly for every failure mode a caller needs
    to treat the same way: bad signature, malformed token, expired ``exp``
    (``JwtService.verify_jws_token`` surfaces this as
    ``jwt.ExpiredSignatureError``, a subclass of ``jwt.InvalidTokenError``),
    a token minted for a different purpose, a malformed ``org_id``, or an
    unrecognized ``role``.

    ``role`` defaults to ``ROLE_MEMBER`` when absent so links minted before
    this claim existed keep working exactly as before.
    """
    from storage.encrypt_utils import get_jwt_service

    try:
        payload = get_jwt_service().verify_jws_token(token)
    except (ValueError, pyjwt.InvalidTokenError) as exc:
        raise ValueError('Invalid or expired sign-up link') from exc
    if payload.get('purpose') != _SIGNUP_LINK_PURPOSE:
        raise ValueError('Invalid sign-up link')
    email = payload.get('email')
    if not email or not isinstance(email, str):
        raise ValueError('Invalid sign-up link')

    org_id: uuid.UUID | None = None
    org_id_raw = payload.get('org_id')
    if org_id_raw is not None:
        try:
            org_id = uuid.UUID(str(org_id_raw))
        except ValueError as exc:
            raise ValueError('Invalid sign-up link') from exc

    role = payload.get('role') or ROLE_MEMBER
    if role not in _SIGNUP_LINK_ROLES:
        raise ValueError('Invalid sign-up link')

    return SignupLinkPayload(email=email.strip().lower(), org_id=org_id, role=role)


async def _get_integrated_idp_provider() -> OAuthProvider | None:
    """Return the integrated/password IDP's ``oauth_providers`` row, if any.

    The single source of truth for whether the integrated IDP is available
    and, when it is, which row backs it: used both to gate every route in
    this module (``is_idp_available`` / ``_require_idp_available``) and to
    resolve the id baked into the ``openhands_auth`` cookie at login
    (``_complete_idp_login``), so the two can never disagree.
    """
    return await OAuthProviderStore().get_first_by_category(INTEGRATED_IDP_CATEGORY)


async def is_idp_available() -> bool:
    """Whether the dev IDP login path is available on this deployment.

    Governed solely by whether migration 179 seeded an ``oauth_providers``
    row for it (which it only does when ``ENABLE_INTEGRATED_IDP`` was set at
    migration time, and never on the managed cloud deployment). Whether a
    real IDP is *also* configured does not affect availability.
    """
    return await _get_integrated_idp_provider() is not None


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
    'link_invalid': (
        'This sign-up link is invalid or has expired. '
        'Please ask an administrator for a new one.'
    ),
}

_FORM_CSS = """    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
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
    input[readonly] {{ color: #a3a3a3; }}
    .value-display {{ width: 100%; box-sizing: border-box; padding: 10px 12px;
             border-radius: 6px; border: 1px solid #404040; background: transparent;
             color: #a3a3a3; font-size: 14px; margin: 0; word-break: break-all; }}
    button {{ width: 100%; margin-top: 20px; padding: 10px; border-radius: 6px;
              border: none; background: #fff; color: #1a1a1a; font-size: 14px;
              font-weight: 500; cursor: pointer; }}
    button:hover {{ opacity: 0.9; }}
    .error {{ margin-top: 0; margin-bottom: 16px; padding: 10px; border-radius: 6px;
              background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3);
              color: #ef4444; font-size: 13px; }}
"""

_FORM_HTML_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenHands — {mode_title}</title>
  <style>
"""
    + _FORM_CSS
    + """  </style>
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
)

_CONFIRM_PASSWORD_HTML = """      <label for="confirm_password">Confirm password</label>
      <input type="password" id="confirm_password" name="confirm_password" required
             minlength="{min_password_length}">
"""

_INVITE_FORM_HTML_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenHands — Set Your Password</title>
  <style>
"""
    + _FORM_CSS
    + """  </style>
</head>
<body>
  <div class="card">
    <h1>Set Your Password</h1>
    <p class="desc">Choose a password to access your OpenHands account.</p>
    {error_html}
    <form method="POST" action="{form_action}">
      <input type="hidden" name="token" value="{token}">
      <label>Email</label>
      <p class="value-display">{email}</p>
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required
             minlength="{min_password_length}" autofocus>
      <label for="confirm_password">Confirm password</label>
      <input type="password" id="confirm_password" name="confirm_password" required
             minlength="{min_password_length}">
      <button type="submit">Set password and sign in</button>
    </form>
  </div>
</body>
</html>"""
)


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
    form_action = f'{web_url}/oauth/{IDP_SIGNUP_PATH if is_signup else IDP_LOGIN_PATH}'
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


def _render_invite_form(
    *, web_url: str, token: str, email: str, error: str = ''
) -> str:
    """Render the set-password form for an admin-issued sign-up link.

    Unlike ``_render_form``, the email is shown as plain, non-editable text
    (sourced from the verified token, never user-editable, and not part of
    the submitted form at all — see ``idp_invite_accept``) and the form
    carries the token itself rather than a ``redirect_url``: there is no
    "sign in" sibling page to link to, since a given link is either usable
    or it isn't.
    """
    error_html = ''
    if error:
        message = _ERROR_MESSAGES.get(error, 'Something went wrong. Please try again.')
        error_html = f'<div class="error">{message}</div>'

    return _INVITE_FORM_HTML_TEMPLATE.format(
        error_html=error_html,
        form_action=f'{web_url}/oauth/{IDP_INVITE_PATH}',
        token=token,
        email=html.escape(email),
        min_password_length=MIN_PASSWORD_LENGTH,
    )


async def _require_idp_available() -> None:
    if not await is_idp_available():
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
    path = IDP_SIGNUP_PATH if mode == 'signup' else IDP_LOGIN_PATH
    target = f'{web_url}/oauth/{path}'
    if redirect_url:
        target = f'{target}?{urlencode({"redirect_url": redirect_url})}'
    return RedirectResponse(target, status_code=302)


@idp_router.get(f'/{IDP_LOGIN_PATH}')
async def idp_login_form(
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
    await _require_idp_available()
    web_url = get_web_url(request)
    if not await UserStore.has_super_admin_with_password():
        return _mode_redirect(web_url, mode='signup', redirect_url=redirect_url)
    html = _render_form(
        mode='login', web_url=web_url, redirect_url=redirect_url, error=error
    )
    return HTMLResponse(content=html)


@idp_router.get(f'/{IDP_SIGNUP_PATH}')
async def idp_signup_form(
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
    await _require_idp_available()
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
    path = IDP_SIGNUP_PATH if mode == 'signup' else IDP_LOGIN_PATH
    params = {'error': error}
    if redirect_url:
        params['redirect_url'] = redirect_url
    target = f'{web_url}/oauth/{path}?{urlencode(params)}'
    return RedirectResponse(target, status_code=302)


@idp_router.post(f'/{IDP_LOGIN_PATH}')
async def idp_login(
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
    await _require_idp_available()
    web_url = get_web_url(request)
    if not await UserStore.has_super_admin_with_password():
        return _mode_redirect(web_url, mode='signup', redirect_url=redirect_url)
    email_str = email.strip().lower()

    user = await UserStore.get_user_by_id(derive_idp_user_id(email_str))
    if user is None:
        user = await UserStore.get_user_by_email(email_str)

    if (
        user is None
        or not user.password_hash
        or not verify_password(password, user.password_hash)
    ):
        logger.info('idp:login_failed', extra={'email': email_str})
        return _form_redirect(
            web_url,
            mode='login',
            error='invalid_credentials',
            redirect_url=redirect_url,
        )

    return await _complete_idp_login(
        request=request,
        user=user,
        is_new_user=False,
        email=email_str,
        redirect_url=redirect_url,
    )


@idp_router.post(f'/{IDP_SIGNUP_PATH}')
async def idp_signup(
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
    await _require_idp_available()
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

    user_id = derive_idp_user_id(email_str)
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
            logger.error('idp:signup_claim_grant_failed', extra={'email': email_str})
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
            logger.error('idp:failed_to_create_user', extra={'email': email_str})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to create user',
            )
        await _set_password_hash(str(user.id), hashed)
        is_new_user = True

    return await _complete_idp_login(
        request=request,
        user=user,
        is_new_user=is_new_user,
        email=email_str,
        redirect_url=redirect_url,
    )


# ── admin-issued sign-up links (OHE-3510) ───────────────────────────────────
#
# A super admin can mint an expiring link (``POST /api/idp/signup-links``,
# see ``idp_invite_router`` below) that lets its recipient set their own
# password and sign in — without the admin ever choosing or seeing that
# password, and without going through the bootstrap-only
# ``/oauth/idp/signup`` form (which is sealed once a super admin with a
# password exists, see the module docstring). This path carries no
# server-side state of its own: the link *is* the credential, a signed JWT
# (``_create_signup_link_token`` / ``_verify_signup_link_token``) naming the
# target email and expiring after ``SIGNUP_LINK_EXPIRY_HOURS`` (72h).
#
# Unlike ``idp_signup``'s one-shot bootstrap guard, there is no "already
# used" check here: if the named email belongs to an existing account, this
# resets its password (whether or not it already had one) rather than
# refusing — minting the link is itself the admin action that authorizes
# the reset, and it's also this IDP's only password-reset path. A link is
# therefore valid, and reusable, for its whole 72-hour window; nothing
# tracks whether it's been used before.
#
# The token also names a ``role``. For an org-scoped role (``member`` /
# ``admin`` / ``owner``), ``_ensure_org_membership`` adds the recipient to
# the token's ``org_id`` with that role once they've set their password —
# a no-op if they're already a member, and never fatal to the
# password-set/login itself (see that function's docstring). For
# ``_SIGNUP_LINK_ROLE_SUPERADMIN`` (never paired with an ``org_id``), the
# recipient is instead granted the instance-level super-admin role via
# ``UserStore.grant_super_admin`` — i.e. a super admin can mint a link that
# creates another super admin, same as ``POST /api/admin/super-admins``
# but self-service for the invitee.


def _invite_form_redirect(web_url: str, *, token: str, error: str) -> RedirectResponse:
    """Redirect back to the invite form with an error, preserving the token."""
    params = {'token': token, 'error': error}
    target = f'{web_url}/oauth/{IDP_INVITE_PATH}?{urlencode(params)}'
    return RedirectResponse(target, status_code=302)


@idp_router.get(f'/{IDP_INVITE_PATH}')
async def idp_invite_form(
    request: Request,
    token: str,
    error: str = '',
):
    """Serve the set-password form for an admin-issued sign-up link.

    Returns ``404`` if this IDP is not available. Returns ``400`` if the
    token is missing/invalid/expired — there is no form to render without a
    valid token to carry forward, unlike the login/sign-up forms' error
    redirects.
    """
    await _require_idp_available()
    web_url = get_web_url(request)
    try:
        link = _verify_signup_link_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_ERROR_MESSAGES['link_invalid'],
        )
    html = _render_invite_form(
        web_url=web_url, token=token, email=link.email, error=error
    )
    return HTMLResponse(content=html)


@idp_router.post(f'/{IDP_INVITE_PATH}')
async def idp_invite_accept(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    """Verify an admin-issued link, set the password, and log in.

    Returns ``404`` if this IDP is not available, ``400`` if the token is
    missing/invalid/expired. Resolves an existing account by derived id,
    then by email — same resolution order as ``idp_signup`` — and, if one
    is found, **resets its password**, regardless of whether it already had
    one: this is how the link doubles as a password-reset mechanism (e.g.
    for a passwordless account pre-created by provisioning, a previous
    invite that was never completed, or an ordinary existing user who needs
    their password reset by an admin). A genuinely new email instead
    creates a brand-new account via ``UserStore.create_user``, exactly like
    the bootstrap sign-up form, including default-org bootstrap and TOS
    auto-acceptance in ``_complete_idp_login``. Whether the recipient ends
    up with an org membership or the instance-level super-admin role
    depends on the token's ``role`` claim, set at mint time by the super
    admin who called ``create_signup_link``: an org-scoped role (with
    ``org_id``) adds the user to that org (see ``_ensure_org_membership``)
    — a no-op if they're already a member — while
    ``_SIGNUP_LINK_ROLE_SUPERADMIN`` instead grants the super-admin role.
    """
    await _require_idp_available()
    web_url = get_web_url(request)
    try:
        link = _verify_signup_link_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_ERROR_MESSAGES['link_invalid'],
        )
    email_str = link.email

    if password != confirm_password:
        return _invite_form_redirect(web_url, token=token, error='password_mismatch')
    if len(password) < MIN_PASSWORD_LENGTH:
        return _invite_form_redirect(web_url, token=token, error='password_too_short')

    user_id = derive_idp_user_id(email_str)
    existing = await UserStore.get_user_by_id(user_id)
    if existing is None:
        existing = await UserStore.get_user_by_email(email_str)

    hashed = hash_password(password)

    user: User | None
    was_password_reset = False
    if existing is not None:
        was_password_reset = existing.password_hash is not None
        await _set_password_hash(str(existing.id), hashed)
        user = existing
        is_new_user = False
    else:
        user_info = {
            'email': email_str,
            'email_verified': True,
            'preferred_username': email_str,
        }
        user = await UserStore.create_user(user_id, user_info)
        if user is None:
            logger.error('idp:invite_failed_to_create_user', extra={'email': email_str})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to create user',
            )
        await _set_password_hash(str(user.id), hashed)
        is_new_user = True

    # was_password_reset distinguishes "reset an existing password" from
    # "claimed a passwordless account" -- both leave is_new_user False, but
    # only the former is a security-relevant event worth being able to spot
    # in logs (someone else's password just changed).
    logger.info(
        'idp:signup_link_accepted',
        extra={
            'user_id': str(user.id),
            'is_new_user': is_new_user,
            'was_password_reset': was_password_reset,
        },
    )

    if link.role == _SIGNUP_LINK_ROLE_SUPERADMIN:
        # Validated at mint time (``create_signup_link``) to never be paired
        # with an ``org_id`` -- granting the instance-level super-admin role
        # is itself the whole point of this branch, not an org membership.
        await UserStore.grant_super_admin(str(user.id))
        logger.info(
            'idp:signup_link_granted_super_admin', extra={'user_id': str(user.id)}
        )
    elif link.org_id is not None:
        await _ensure_org_membership(link.org_id, user, link.role)

    return await _complete_idp_login(
        request=request,
        user=user,
        is_new_user=is_new_user,
        email=email_str,
        redirect_url='',
    )


async def _ensure_org_membership(org_id: uuid.UUID, user: User, role_name: str) -> None:
    """Add ``user`` to ``org_id`` with ``role_name``, if not already a member.

    A no-op if the user is already a member — "no change" is the whole
    point of checking first, both to honor that contract and because
    ``OrgMemberStore.add_user_to_org`` would otherwise raise on the
    duplicate (org_id, user_id) primary key. Never raises: a failure here
    (missing org, missing ``role_name`` role, LiteLLM integration error) is
    logged and swallowed rather than blocking the password-set/login that
    already succeeded — the org add is a bonus on top of that, not a
    precondition for it.
    """
    existing_member = await OrgMemberStore.get_org_member(org_id, user.id)
    if existing_member is not None:
        return

    org = await OrgStore.get_org_by_id(org_id)
    if org is None:
        logger.warning(
            'idp:signup_link_org_not_found',
            extra={'org_id': str(org_id), 'user_id': str(user.id)},
        )
        return

    role = await RoleStore.get_role_by_name(role_name)
    if role is None:
        logger.error('idp:signup_link_role_missing', extra={'role_name': role_name})
        return

    try:
        settings = await OrgService.create_litellm_integration(org_id, str(user.id))
        llm_api_key_secret = settings.agent_settings.llm.api_key
        llm_api_key = (
            llm_api_key_secret.get_secret_value() if llm_api_key_secret else ''  # type: ignore[union-attr]
        )
        await OrgMemberStore.add_user_to_org(
            org_id=org_id,
            user_id=user.id,
            role_id=role.id,
            llm_api_key=llm_api_key,
            status='active',
            agent_settings_diff={},
            conversation_settings_diff={},
        )
    except Exception:
        logger.exception(
            'idp:signup_link_add_to_org_failed',
            extra={'org_id': str(org_id), 'user_id': str(user.id)},
        )
        return

    logger.info(
        'idp:signup_link_added_to_org',
        extra={'org_id': str(org_id), 'user_id': str(user.id)},
    )


class CreateSignupLinkRequest(BaseModel):
    """Body for ``POST /api/idp/signup-links``.

    ``role`` is one of the org-scoped roles (``member`` / ``admin`` /
    ``owner``) or the instance-level ``superadmin``. An org-scoped role
    requires ``org_id`` (the recipient is added to that org with it);
    ``superadmin`` instead grants the minting caller's own instance-level
    role and must not be combined with ``org_id`` -- both are enforced by
    ``create_signup_link`` below, not by this model, since the valid
    combination depends on which role was chosen.
    """

    email: EmailStr
    org_id: uuid.UUID | None = None
    role: str = ROLE_MEMBER

    @field_validator('role')
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in _SIGNUP_LINK_ROLES:
            raise ValueError(
                f'role must be one of {sorted(_SIGNUP_LINK_ROLES)!r}, got {value!r}'
            )
        return value


class SignupLinkResponse(BaseModel):
    """Result of minting an admin-issued sign-up link."""

    url: str
    expires_at: datetime
    role: str


def _idp_invite_router() -> APIRouter:
    """Router for the super-admin sign-up-link minting endpoint."""
    router = APIRouter(prefix='/api/idp', tags=['IDP'])

    @router.post(
        '/signup-links',
        response_model=SignupLinkResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_signup_link(
        request: Request,
        body: CreateSignupLinkRequest,
        user_id: str | None = Depends(get_user_id),
    ) -> SignupLinkResponse:
        # Availability (404) is checked before authentication/permission so
        # the feature's existence leaks no information when disabled --
        # same ordering as the self-service password endpoints above.
        await _require_idp_available()
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Not authenticated',
            )
        await authorize_permission(request, user_id, Permission.CREATE_SIGNUP_LINK)

        if body.role == _SIGNUP_LINK_ROLE_SUPERADMIN:
            if body.org_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='A super-admin sign-up link cannot be scoped to an organization',
                )
        elif body.role in (ROLE_ADMIN, ROLE_OWNER) and body.org_id is None:
            # ``member`` stays valid without an ``org_id`` -- the pre-existing
            # behavior of a link that only creates/resets the account with no
            # org added. ``admin``/``owner`` are meaningless without an org to
            # hold that elevated role in, so they require one explicitly.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"org_id is required when role is '{body.role}'",
            )

        if body.org_id is not None:
            org = await OrgStore.get_org_by_id(body.org_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail='Organization not found',
                )

        email_str = body.email.strip().lower()
        token = _create_signup_link_token(email_str, org_id=body.org_id, role=body.role)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=SIGNUP_LINK_EXPIRY_HOURS
        )
        web_url = get_web_url(request)
        url = f'{web_url}/oauth/{IDP_INVITE_PATH}?{urlencode({"token": token})}'

        logger.info(
            'idp:signup_link_created',
            extra={
                'caller_user_id': user_id,
                'email': email_str,
                'org_id': str(body.org_id) if body.org_id else None,
                'role': body.role,
            },
        )
        return SignupLinkResponse(url=url, expires_at=expires_at, role=body.role)

    return router


idp_invite_router = _idp_invite_router()


# ── shared post-authentication steps (mirrors the real OAuth v2 callback) ──


async def _complete_idp_login(
    *,
    request: Request,
    user: User,
    is_new_user: bool,
    email: str,
    redirect_url: str,
) -> RedirectResponse:
    """Finish a successful dev IDP login/sign-up: TOS, org, analytics, cookie.

    Shared by ``idp_login`` (existing account) and ``idp_signup``
    (brand-new account) so both end up with identical post-auth behavior.
    """
    provider = await _get_integrated_idp_provider()
    if provider is None:
        # Should not happen -- every route reaching here already passed
        # ``_require_idp_available()`` -- but fail the same way if the row
        # disappeared between that check and this one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Password login is not available',
        )

    user_id = str(user.id)

    # Auto-accept TOS in dev mode — the dev IDP is for trial/dev only and
    # requiring TOS acceptance adds friction without security value.
    has_accepted_tos = user.accepted_tos is not None
    if not has_accepted_tos:
        await _accept_tos_for_idp_user(user_id)
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
                'idp:default_org_bootstrap_failed',
                extra={'user_id': user_id},
                stack_info=True,
            )

    # Best-effort analytics identify — never block login on analytics.
    try:
        await _track_idp_login(user_id, email)
    except Exception:
        logger.exception('idp:analytics_failed', stack_info=True)

    web_url = get_web_url(request)
    final_redirect_url = redirect_url or '/'

    # Check onboarding redirect (self-hosted: only the first owner/super-admin).
    should_onboard = await _should_redirect_to_onboarding_idp_user(user_id, user)
    if should_onboard:
        from server.routes.auth import _build_onboarding_redirect

        final_redirect_url = _build_onboarding_redirect(final_redirect_url, web_url)
    else:
        from server.routes.auth import _build_cross_app_redirect_url

        final_redirect_url = _build_cross_app_redirect_url(final_redirect_url, web_url)

    response = RedirectResponse(final_redirect_url, status_code=302)

    _set_idp_cookie(
        request=request,
        response=response,
        user_id=user_id,
        accepted_tos=has_accepted_tos,
        secure=web_url.startswith('https'),
        idp_provider_id=provider.id,
    )

    logger.info(
        'idp:user_logged_in',
        extra={'user_id': user_id, 'is_new_user': is_new_user},
    )
    return response


# ── status endpoint (for config injection) ─────────────────────────────────


def _idp_status_router() -> APIRouter:
    """Create a separate router for the status endpoint at /api/idp/status."""
    router = APIRouter(prefix='/api/idp', tags=['IDP'])

    @router.get('/status')
    async def idp_status() -> JSONResponse:
        enabled = await is_idp_available()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'enabled': enabled},
        )

    return router


idp_status_router = _idp_status_router()


# ── self-service password set/change (for already-authenticated users) ─────
#
# Distinct from the sign-up/login forms above: those are unauthenticated and
# only ever create/claim *the* super admin as a one-time bootstrap step. These
# two endpoints let any already-logged-in user set a password where they have
# none yet, or change the one they have — independent of how they originally
# authenticated (dev IDP sign-up, a real OAuth/OIDC IDP, or an admin-issued
# invite link). They live under the same ``/api/idp`` prefix as the status
# endpoint (JSON APIs, not the HTML form flow under ``/oauth``), and are
# equally gated by the integrated-IDP row's existence — see
# ``_require_idp_available``.


class SetPasswordRequest(BaseModel):
    """Body for ``POST /api/idp/password``.

    ``current_password`` is required only when the user already has one set
    (checked server-side against ``User.password_hash``) — callers can't
    determine this themselves without ``GET /api/idp/password`` first, but
    the server re-validates regardless of what the client believes.
    """

    current_password: str | None = None
    new_password: str
    confirm_password: str


def _idp_password_router() -> APIRouter:
    """Router for the authenticated has-password/set-password endpoints."""
    router = APIRouter(prefix='/api/idp', tags=['IDP'])

    @router.get('/password')
    async def idp_has_password(
        user_id: str | None = Depends(get_user_id),
    ) -> JSONResponse:
        await _require_idp_available()
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Not authenticated',
            )
        user = await UserStore.get_user_by_id(user_id)
        has_password = bool(user and user.password_hash)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'has_password': has_password},
        )

    @router.post('/password')
    async def idp_set_password(
        request: Request,
        body: SetPasswordRequest,
        user_id: str | None = Depends(get_user_id),
    ) -> JSONResponse:
        await _require_idp_available()
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Not authenticated',
            )

        # Throttles guessing of the current password; keyed by user_id since
        # the caller is already authenticated.
        await check_rate_limit_by_user_id(
            request=request,
            key_prefix='idp_set_password',
            user_id=user_id,
            user_rate_limit_seconds=RATE_LIMIT_SET_PASSWORD_USER_SECONDS,
            ip_rate_limit_seconds=RATE_LIMIT_SET_PASSWORD_IP_SECONDS,
        )

        if body.new_password != body.confirm_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='New password and confirmation do not match',
            )
        if len(body.new_password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Password must be at least {MIN_PASSWORD_LENGTH} characters long',
            )

        user = await UserStore.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
            )

        if user.password_hash:
            if not body.current_password or not verify_password(
                body.current_password, user.password_hash
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Current password is incorrect',
                )

        await _set_password_hash(user_id, hash_password(body.new_password))

        logger.info('idp:password_set', extra={'user_id': user_id})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'message': 'Password updated'},
        )

    return router


idp_password_router = _idp_password_router()


# ── helpers ────────────────────────────────────────────────────────────────


def _set_idp_cookie(
    request: Request,
    response: RedirectResponse,
    user_id: str,
    accepted_tos: bool,
    secure: bool,
    idp_provider_id: int,
) -> None:
    """Set the ``openhands_auth`` JWT cookie for a dev IDP session.

    Mirrors ``_set_oauth_v2_cookie`` from ``oauth_v2.py`` but with no IDP
    token expiry (the integrated IDP has no external token to refresh).
    ``idp_provider_id`` is still baked in (the integrated IDP's real
    ``oauth_providers.id``, resolved by the caller) so IDP-token refresh can
    identify this session the same uniform way as a real external IDP.
    """
    from server.auth.oauth_v2_refresh import COOKIE_MAX_AGE_CAP_SECONDS

    max_age_seconds = COOKIE_MAX_AGE_CAP_SECONDS
    payload = create_oauth_v2_cookie_payload(
        user_id,
        access_token_expires_at=None,
        accepted_tos=accepted_tos,
        idp_provider_id=idp_provider_id,
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


async def _accept_tos_for_idp_user(user_id: str) -> None:
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


async def _track_idp_login(user_id: str, email: str) -> None:
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
        idp='idp',
        orgs=[],
    )
    analytics.track_user_logged_in(ctx=ctx, idp='idp')


async def _should_redirect_to_onboarding_idp_user(user_id: str, user) -> bool:
    """Check onboarding redirect for dev IDP users.

    Delegates to the shared ``_should_redirect_to_onboarding`` logic from
    the auth routes so behavior is consistent with the Keycloak/OAuth path.
    """
    from server.routes.auth import _should_redirect_to_onboarding

    return await _should_redirect_to_onboarding(user_id, user)
