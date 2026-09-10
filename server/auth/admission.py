"""Shared account admission, invitations, and post-login navigation."""

from urllib.parse import (
    parse_qs,
    parse_qsl,
    quote,
    urlencode,
    urlparse,
    urlsplit,
    urlunparse,
    urlunsplit,
)
from uuid import UUID

from server.auth.browser_security import safe_redirect
from server.auth.contracts import InvalidCredentials, UserProfile
from server.auth.user.default_user_authorizer import DefaultUserAuthorizer
from server.constants import DEPLOYMENT_MODE
from server.logger import logger
from server.services.org_invitation_service import (
    EmailMismatchError,
    InvitationExpiredError,
    InvitationInvalidError,
    OrgInvitationService,
    UserAlreadyMemberError,
)
from storage.default_org_service import DefaultOrgBootstrapService
from storage.user import User
from storage.user_store import UserStore


def account_profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        email_verified=bool(user.email_verified),
        is_disabled=bool(user.is_disabled),
        role_id=user.role_id,
    )


async def check_account_admission(user: User) -> None:
    if user.is_disabled:
        raise InvalidCredentials('Account is unavailable')
    authorization = await DefaultUserAuthorizer(
        prevent_duplicates=False
    ).authorize_user(account_profile(user))
    if not authorization.success:
        raise InvalidCredentials(authorization.error_detail or 'Account is unavailable')


def _with_flag(destination: str, flag: str) -> str:
    parsed = urlsplit(destination)
    query = parse_qsl(parsed.query, keep_blank_values=True) + [(flag, 'true')]
    return urlunsplit(parsed._replace(query=urlencode(query)))


async def apply_login_memberships(
    user: User,
    redirect_url: str,
    invitation_token: str | None = None,
    *,
    is_new_user: bool = False,
) -> tuple[User, str]:
    """Verified invitations precede default membership so their roles win."""
    user_id = str(user.id)
    if invitation_token:
        try:
            await OrgInvitationService.accept_invitation(invitation_token, user.id)
        except InvitationExpiredError:
            redirect_url = _with_flag(redirect_url, 'invitation_expired')
        except InvitationInvalidError:
            redirect_url = _with_flag(redirect_url, 'invitation_invalid')
        except UserAlreadyMemberError:
            redirect_url = _with_flag(redirect_url, 'already_member')
        except EmailMismatchError:
            redirect_url = _with_flag(redirect_url, 'email_mismatch')
        else:
            redirect_url = _with_flag(redirect_url, 'invitation_success')
            user = await UserStore.get_user_by_id(user_id) or user
    if user.email_verified:
        await OrgInvitationService.accept_pending_invitations_for_user(user)
        user = await UserStore.get_user_by_id(user_id) or user
    user = await DefaultOrgBootstrapService.apply_for_user(
        user, is_new_user=is_new_user
    )
    if user.current_org_id is not None:
        from server.auth.user_management import EnterpriseUserManagementService

        await EnterpriseUserManagementService().ensure_llm_provisioned(
            user.id, user.current_org_id
        )
    return user, redirect_url


async def complete_local_login(
    user_id: UUID, redirect_url: str, invitation_token: str | None = None
) -> str:
    """Admit an explicitly provisioned local account without requiring SMTP."""
    redirect_url = safe_redirect(redirect_url)
    user = await UserStore.get_user_by_id(str(user_id))
    if user is None:
        raise InvalidCredentials('Account is unavailable')
    await check_account_admission(user)
    user, redirect_url = await apply_login_memberships(
        user, redirect_url, invitation_token
    )
    await UserStore.record_login(str(user_id))
    from server.auth.login_analytics import schedule_login_analytics

    schedule_login_analytics(user)
    if user.accepted_tos is None:
        params = {'redirect_url': redirect_url}
        if invitation_token:
            params['invitation_token'] = invitation_token
        return '/accept-tos?' + urlencode(params)
    return await _get_post_auth_redirect(str(user_id), redirect_url, '', user)


def _extract_login_inner_return_to(relative_url: str) -> str | None:
    """Extract the inner ``returnTo`` from a ``/login?returnTo=...`` URL.

    Returns the decoded inner ``returnTo`` value, or ``None`` if
    ``relative_url`` is not a login URL or has no inner ``returnTo``.

    The OAuth flow's ``state`` is set to the full URL of the page that
    triggered the login (see ``generateAuthUrl`` in the frontend).
    For an unauthenticated deep-link visit, that page is itself
    ``/login?returnTo=<actual destination>`` (or legacy
    ``/login?redirect=<actual destination>``), so the OAuth callback's
    ``redirect_url`` ends up *wrapping* the user's true destination
    inside a login URL. Sending the user back through ``/login`` after
    onboarding works in principle (``LoginPage`` re-redirects authed
    users to its own ``returnTo``), but the round-trip adds extra
    state and is brittle when query-string layering goes wrong.

    Unwrapping here keeps the post-onboarding navigation a single
    direct step, e.g. ``/onboarding?returnTo=%2Fsettings%2Fuser``
    rather than the doubly-nested
    ``/onboarding?returnTo=%2Flogin%3FreturnTo%3D%252Fsettings...``.
    """
    parsed = urlparse(relative_url)
    if parsed.path != '/login':
        return None
    query = parse_qs(parsed.query)
    inner = query.get('returnTo') or query.get('redirect')
    if not inner:
        return None
    value = inner[0]
    if not value.startswith('/'):
        return None
    return value


def _is_cross_app_relative_path(value: str) -> bool:
    """Return whether ``value`` is a safe same-origin cross-app route."""
    if not value.startswith('/') or value.startswith('//'):
        return False
    parsed = urlparse(value)
    return parsed.path in ('/automations', '/canvas') or parsed.path.startswith(
        ('/automations/', '/canvas/')
    )


def _merge_login_wrapper_query(inner_destination: str, outer_query: str) -> str:
    """Move non-routing login-wrapper query params onto an unwrapped destination."""
    extra_params = [
        (key, value)
        for key, value in parse_qsl(outer_query, keep_blank_values=True)
        if key not in ('returnTo', 'redirect')
    ]
    if not extra_params:
        return inner_destination

    parsed_inner = urlparse(inner_destination)
    query = parse_qsl(parsed_inner.query, keep_blank_values=True) + extra_params
    return urlunparse(parsed_inner._replace(query=urlencode(query)))


def _build_cross_app_redirect_url(redirect_url: str, web_url: str) -> str:
    """Build a direct server-side redirect for same-origin microservice routes.

    OAuth state often points back at the main app's ``/login`` page with the
    real destination nested inside ``returnTo`` or the legacy ``redirect`` query
    parameter. For paths owned by another frontend, such as ``/automations`` or
    ``/canvas``, sending the browser through the main app SPA first is brittle:
    any old or already-loaded bundle can client-navigate and show the main app
    404 before ingress sees the route.

    Returning a direct ``Location: <web_url>/<cross-app>...`` from the backend
    makes the browser issue a real document request, so ingress routes it to the
    owning service.
    """
    if not redirect_url:
        return redirect_url

    relative = redirect_url
    if web_url and redirect_url.startswith(web_url):
        relative = redirect_url[len(web_url) :] or '/'

    parsed = urlparse(relative)
    if parsed.path == '/login':
        query = parse_qs(parsed.query)
        inner = query.get('returnTo') or query.get('redirect')
        if inner and _is_cross_app_relative_path(inner[0]):
            destination = _merge_login_wrapper_query(inner[0], parsed.query)
            return f'{web_url}{destination}'

    if _is_cross_app_relative_path(relative):
        return f'{web_url}{relative}'

    return redirect_url


def _build_onboarding_redirect(original_url: str, web_url: str) -> str:
    """Build the ``/onboarding`` redirect URL preserving ``returnTo``.

    The user's originally requested destination is preserved as a
    ``returnTo`` query parameter on ``/onboarding``.

    Without this, any deep link the user clicked while logged out
    (e.g. ``/conversations/abc?foo=bar``) is silently dropped at the
    onboarding interstitial because the OAuth callback would clobber
    its working ``redirect_url`` with a bare ``f'{web_url}/onboarding'``.
    The frontend ``OnboardingForm`` reads this ``returnTo`` query
    parameter and restores it after the user finishes the form.

    The trivial home-page case (``original_url`` empty, equal to
    ``web_url``, or pointing at ``web_url/``) returns the bare
    ``/onboarding`` URL to keep the URL bar clean — that is already
    the default landing page once onboarding completes.

    The ``returnTo`` value is always a *relative* path (``/foo?bar``)
    rather than an absolute URL: that keeps the URL short, avoids
    leaking the deployment origin into the browser bar a second time,
    and lets the frontend use ``navigate(returnTo)`` directly.

    When ``original_url`` is itself a ``/login?returnTo=...`` URL —
    or a legacy ``/login?redirect=...`` URL — which is the common case for
    unauthenticated deep-link visits,
    because the OAuth flow's ``state`` carries the full login page
    URL — the *inner* ``returnTo`` is extracted so the user lands at
    their real destination in a single navigation rather than
    bouncing through ``/login`` after onboarding.
    """
    onboarding_url = f'{web_url}/onboarding'
    if not original_url:
        return onboarding_url

    # Compute the path-and-query portion of the original URL. We try
    # to strip the deployment origin first so we end up with a
    # relative path; if the URL points at a different host we fall
    # back to the URL as-is. The ``OnboardingForm`` component's
    # ``sanitizeReturnTo`` helper rejects absolute/protocol-relative
    # URLs before use, so any unexpected absolute value here is safe.
    relative = original_url
    if web_url and original_url.startswith(web_url):
        relative = original_url[len(web_url) :] or '/'

    # If we ended up with a login-page URL, unwrap its inner
    # ``returnTo`` so post-onboarding navigation goes straight to the
    # user's real destination instead of bouncing through ``/login``.
    inner_return_to = _extract_login_inner_return_to(relative)
    if inner_return_to is not None:
        relative = inner_return_to

    # Skip the trivial home-page case to keep the URL clean.
    if relative in ('', '/'):
        return onboarding_url

    return f'{onboarding_url}?returnTo={quote(relative, safe="")}'


async def _should_redirect_to_onboarding(user_id: str, user: User) -> bool:
    """Check if user should be redirected to onboarding after TOS acceptance.
    Backend always redirects applicable users to /onboarding.
    Returns True if:
    - User has onboarding_completed explicitly set to False (new users)
    - Either:
      - Deployment mode is 'cloud' (all users)
      - Deployment mode is 'self_hosted' AND user is the super admin
        (first owner in their current org to accept TOS)

    Returns False if:
    - User has onboarding_completed=True (already completed)
    - User has onboarding_completed=None (existing users before this feature)
    """
    # Already completed onboarding
    if user.onboarding_completed is True:
        return False

    # Existing user before this feature (NULL in database)
    if user.onboarding_completed is None:
        return False

    # Cloud SaaS: all users go to onboarding
    if DEPLOYMENT_MODE == 'cloud':
        return True

    # Self-hosted SaaS: only the super admin (first owner to accept TOS in the org)
    if DEPLOYMENT_MODE == 'self_hosted':
        first_owner = await UserStore.get_first_owner_in_org(user.current_org_id)
        if first_owner and str(first_owner.id) == user_id:
            return True

    return False


async def _get_post_auth_redirect(
    user_id: str, default_url: str, web_url: str, user: User | None = None
) -> str:
    """Determine where to redirect user after authentication completes.

    Called after offline token is stored to determine final redirect destination.
    Checks for pending user flows (e.g., onboarding) before falling back to default.

    Args:
        user_id: The user's ID.
        default_url: The default URL to redirect to if no special flow is needed.
        web_url: The base web URL for constructing absolute paths.
        user: Optional user object to avoid refetching.

    Returns:
        The URL to redirect the user to.
    """
    if not user:
        user = await UserStore.get_user_by_id(user_id)
    if user and await _should_redirect_to_onboarding(user_id, user):
        logger.info(
            'Redirecting user to onboarding',
            extra={'user_id': user_id, 'deployment_mode': DEPLOYMENT_MODE},
        )
        # Preserve the user's originally requested destination as
        # ``?returnTo=...`` so the frontend ``OnboardingForm`` can
        # restore it after the user finishes the form.
        return _build_onboarding_redirect(default_url, web_url)
    return _build_cross_app_redirect_url(default_url, web_url)
