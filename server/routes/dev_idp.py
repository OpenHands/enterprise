"""Development-only insecure IDP — email-only login for self-hosted/dev setups.

This module provides a login path that does **not** depend on Keycloak or any
external identity provider. It is intended for self-hosted / trial installs
that have no real IDP configured yet.

How it works:

* The user submits an email address (no password, no validation).
* A deterministic ``user_id`` is derived from the email via
  ``uuid.uuid5(NAMESPACE_DNS, email)`` (SHA-1 based, stable across logins).
* If a ``User`` row already exists for that id (or for that email), it is
  reused so settings survive across logins and across a later switch to a
  real IDP (the existing ``allow_match_by_email`` / email-match mechanism
  in ``oauth_v2._resolve_or_create_user`` handles the IDP swap-over).
* The same small JWT cookie (``openhands_auth``) used by the OAuth v2 path
  is set, so downstream middleware and ``SaasUserAuth`` treat the session
  identically to an OAuth v2 cookie session.

Availability rules:

* Only available when ``DEPLOYMENT_MODE == 'self_hosted'``.
* Automatically disabled once a real IDP is configured in the
  ``oauth_providers`` table (any row with ``is_idp = True``). At that point
  the status endpoint returns ``{"enabled": false}`` and the login endpoint
  returns ``404``.

**This IDP is intentionally insecure.** It must never be enabled on cloud
(``app.all-hands.dev``) or any deployment where security matters.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.oauth_v2_refresh import (
    COOKIE_MAX_AGE_CAP_SECONDS,
    create_oauth_v2_cookie_payload,
    sign_oauth_v2_cookie,
)
from server.constants import DEPLOYMENT_MODE
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.default_org_service import DefaultOrgBootstrapService
from storage.oauth_provider_store import OAuthProviderStore
from storage.user_store import UserStore

dev_idp_router = APIRouter(prefix='/api/dev-idp', tags=['Dev IDP'])

# Fixed namespace for deterministic user-id derivation from email.
# Using a custom namespace instead of the well-known DNS namespace keeps
# dev-IDP user ids from colliding with any uuid5(NAMESPACE_DNS, ...) that
# an external system might independently derive.
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
    * No real IDP is configured in ``oauth_providers`` (no row with
      ``is_idp = True``). Once an admin configures a real IDP, the dev
      IDP is disabled.
    """
    if DEPLOYMENT_MODE != 'self_hosted':
        return False
    idp_providers = await OAuthProviderStore().get_idp_providers()
    return len(idp_providers) == 0


@dev_idp_router.get('/status')
async def dev_idp_status() -> JSONResponse:
    """Return whether the dev IDP login path is available.

    The frontend uses this to decide whether to show the email-only login
    form on the login page.
    """
    enabled = await is_dev_idp_available()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={'enabled': enabled},
    )


class DevIdpLoginRequest(BaseModel):
    """Payload for ``POST /api/dev-idp/login``.

    ``email`` is the only required field. ``redirect_url`` is optional and
    defaults to ``/`` (the app root).
    """

    email: EmailStr
    redirect_url: str = '/'


@dev_idp_router.post('/login')
async def dev_idp_login(request: Request, body: DevIdpLoginRequest):
    """Log in via the development IDP (email-only, no password).

    Derives a deterministic user id from the email, resolves or creates the
    ``User`` row, auto-accepts TOS (dev mode), and sets the ``openhands_auth``
    JWT cookie — the same cookie used by the OAuth v2 path.

    Returns a JSON response with ``redirect_url`` so the frontend can
    navigate the browser to the user's intended destination.
    """
    if not await is_dev_idp_available():
        return _dev_idp_unavailable_error()

    email = body.email.strip().lower()
    user_id = derive_dev_idp_user_id(email)

    # Try to resolve an existing user — first by the derived id, then by
    # email. The email fallback handles users who were created by a real
    # IDP (their user id is the external IDP's sub, not our hash) but
    # are now logging in through the dev IDP.
    user = await UserStore.get_user_by_id(user_id)
    if user is None:
        user_by_email = await UserStore.get_user_by_email(email)
        if user_by_email is not None:
            user_id = str(user_by_email.id)
            user = user_by_email

    is_new_user = user is None
    if is_new_user:
        user_info = {
            'email': email,
            'email_verified': True,
            'preferred_username': email,
        }
        created = await UserStore.create_user(user_id, user_info)
        if created is None:
            logger.error('dev_idp:failed_to_create_user', extra={'email': email})
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={'error': 'Failed to create user'},
            )
        user = created
        user_id = str(user.id)

    assert user is not None  # narrowed: either existing or just created

    # Auto-accept TOS in dev mode — the dev IDP is for trial/dev only and
    # requiring TOS acceptance adds friction without security value.
    # Also set analytics consent so the identify call is not a no-op.
    has_accepted_tos = user.accepted_tos is not None
    if not has_accepted_tos:
        await _accept_tos_for_dev_user(user_id)

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
    redirect_url = body.redirect_url or '/'

    # Check onboarding redirect (self-hosted: only the first owner/super-admin).
    should_onboard = await _should_redirect_to_onboarding_dev(user_id, user)
    if should_onboard:
        from server.routes.auth import _build_onboarding_redirect

        redirect_url = _build_onboarding_redirect(redirect_url, web_url)
    else:
        from server.routes.auth import _build_cross_app_redirect_url

        redirect_url = _build_cross_app_redirect_url(redirect_url, web_url)

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={'redirect_url': redirect_url},
    )

    _set_dev_idp_cookie(
        request=request,
        response=response,
        user_id=user_id,
        accepted_tos=True,
        secure=web_url.startswith('https'),
    )

    logger.info(
        'dev_idp:user_logged_in',
        extra={'user_id': user_id, 'is_new_user': is_new_user},
    )
    return response


def _set_dev_idp_cookie(
    request: Request,
    response: JSONResponse | RedirectResponse,
    user_id: str,
    accepted_tos: bool,
    secure: bool,
) -> None:
    """Set the ``openhands_auth`` JWT cookie for a dev IDP session.

    Mirrors ``_set_oauth_v2_cookie`` from ``oauth_v2.py`` but with no IDP
    token expiry (the dev IDP has no external token to refresh). The cookie
    carries only ``user_id``, ``accepted_tos``, and ``iat``.
    """
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


def _dev_idp_unavailable_error() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            'error': (
                'Development IDP is not available. '
                'It is only enabled on self-hosted deployments with no '
                'real IDP configured.'
            )
        },
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
