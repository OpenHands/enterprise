import base64
import json
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Annotated, Optional, cast
from urllib.parse import quote, urlencode, urlparse
from uuid import UUID as parse_uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from openhands.analytics import get_analytics_service, resolve_analytics_context
from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderHandler,
    ProviderToken,
)
from openhands.app_server.integrations.service_types import ProviderType, TokenResponse
from openhands.app_server.user_auth.user_auth import AuthType, get_user_auth
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.admission import (
    _build_cross_app_redirect_url as _build_cross_app_redirect_url,
)
from server.auth.admission import (
    _build_onboarding_redirect as _build_onboarding_redirect,
)
from server.auth.admission import (
    _get_post_auth_redirect as _get_post_auth_redirect,
)
from server.auth.admission import (
    _should_redirect_to_onboarding as _should_redirect_to_onboarding,
)
from server.auth.admission import (
    apply_login_memberships,
)
from server.auth.auth_error import TokenRefreshError
from server.auth.browser_security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    clear_session_cookie,
    safe_redirect,
    validate_csrf,
)
from server.auth.constants import (
    KEYCLOAK_CLIENT_ID,
    KEYCLOAK_REALM_NAME,
    KEYCLOAK_SERVER_URL_EXT,
    RECAPTCHA_SITE_KEY,
    ROLE_CHECK_ENABLED,
)
from server.auth.contracts import (
    AuthenticationUnavailable,
    UserProfile,
)
from server.auth.cookie_chunking import (
    delete_chunked_cookie,
    read_chunked_cookie,
    set_chunked_cookie,
)
from server.auth.gitlab_sync import schedule_gitlab_repo_sync
from server.auth.identities import IdentityRepository
from server.auth.keycloak.authorization import (
    require_keycloak,
    validate_return_destination,
)
from server.auth.login_analytics import (
    _get_user_orgs_with_data as _get_user_orgs_with_data,
)
from server.auth.login_analytics import (
    _track_login_analytics_background as _track_login_analytics_background,
)
from server.auth.mode import AuthMode, get_auth_mode
from server.auth.recaptcha_service import recaptcha_service
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import TokenManager
from server.auth.user.user_authorizer import (
    UserAuthorizer,
    depends_user_authorizer,
)
from server.constants import (
    DEPLOYMENT_MODE,
    IS_FEATURE_ENV,
)
from server.utils.conversation_utils import get_session_api_key, get_user_id
from server.utils.rate_limit_utils import (
    RATE_LIMIT_AUTH_VERIFY_EMAIL_IP_SECONDS,
    RATE_LIMIT_AUTH_VERIFY_EMAIL_USER_SECONDS,
    check_rate_limit_by_user_id,
)
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.database import a_session_maker
from storage.user import User
from storage.user_store import UserStore

with warnings.catch_warnings():
    warnings.simplefilter('ignore')

api_router = APIRouter(prefix='/api')
oauth_router = APIRouter(prefix='/oauth')

token_manager = TokenManager()


async def _get_keycloak_tokens_or_unavailable(
    code: str, redirect_uri: str
) -> tuple[str | None, str | None]:
    try:
        return await token_manager.get_keycloak_tokens(code, redirect_uri)
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Authentication service temporarily unavailable',
            headers={'Retry-After': '1'},
        ) from exc


def create_provider_tokens_object(
    providers_set: list[ProviderType],
) -> PROVIDER_TOKEN_TYPE:
    """Create provider tokens object for the given providers."""
    provider_information: dict[ProviderType, ProviderToken] = {}

    for provider in providers_set:
        provider_information[provider] = ProviderToken(token=None, user_id=None)

    return MappingProxyType(provider_information)


def set_response_cookie(
    request: Request,
    response: Response,
    keycloak_access_token: str,
    keycloak_refresh_token: str,
    secure: bool = True,
    accepted_tos: bool = False,
):
    # Create a signed JWT token
    cookie_data = {
        'access_token': keycloak_access_token,
        'refresh_token': keycloak_refresh_token,
        'accepted_tos': accepted_tos,
    }
    from storage.encrypt_utils import get_jwt_service

    signed_token = get_jwt_service().create_jws_token(
        cookie_data, expires_in=timedelta(weeks=1)
    )

    # Set secure cookie with signed token. The value can exceed the
    # browser's 4096-byte single-cookie cap for users with large Keycloak
    # claim sets, so write it through the chunked-cookie helper, which
    # splits oversized values across sibling cookies and stays
    # byte-identical for values that fit in one cookie.
    response.delete_cookie(
        CSRF_COOKIE, secure=True, httponly=False, samesite='lax', path='/'
    )
    set_chunked_cookie(
        response,
        'keycloak_auth',
        signed_token,
        domain=get_cookie_domain(),
        secure=secure,
        httponly=True,
        samesite=get_cookie_samesite(),
    )


def _extract_oauth_state(
    state: str | None,
) -> tuple[str, str | None, str | None, str | None]:
    """Extract redirect URL, reCAPTCHA token, invitation token, and link provider.

    Returns:
        Tuple of (redirect_url, recaptcha_token, invitation_token, link_provider).
        Tokens may be None. ``link_provider`` is only set on the return leg of a
        post-auth git provider link (``kc_action=idp_link:<provider>``).
    """
    if not state:
        return '', None, None, None

    try:
        # Try to decode as JSON (new format with reCAPTCHA and/or invitation)
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        return (
            state_data.get('redirect_url', ''),
            state_data.get('recaptcha_token'),
            state_data.get('invitation_token'),
            state_data.get('link_provider'),
        )
    except Exception:
        # Old format - state is just the redirect URL
        return state, None, None, None


@oauth_router.get('/keycloak/callback')
async def keycloak_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    kc_action_status: Optional[str] = None,
    user_authorizer: UserAuthorizer = depends_user_authorizer(),
):
    require_keycloak()
    # Extract redirect URL, reCAPTCHA token, invitation token, and link provider
    redirect_url, recaptcha_token, invitation_token, link_provider = (
        _extract_oauth_state(state)
    )

    redirect_url = validate_return_destination(redirect_url, get_web_url(request))

    if redirect_url is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing state in request params',
        )

    if not code:
        # check if this is a forward from the account linking page
        if (
            error == 'temporarily_unavailable'
            and error_description == 'authentication_expired'
        ):
            return RedirectResponse(redirect_url, status_code=302)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing code in request params',
        )

    web_url = get_web_url(request)
    redirect_uri = web_url + request.url.path

    (
        keycloak_access_token,
        keycloak_refresh_token,
    ) = await _get_keycloak_tokens_or_unavailable(code, redirect_uri)
    if not keycloak_access_token or not keycloak_refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Problem retrieving Keycloak tokens',
        )

    try:
        user_info = await token_manager.get_user_info(keycloak_access_token)
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Authentication service temporarily unavailable',
            headers={'Retry-After': '1'},
        ) from exc
    if ROLE_CHECK_ENABLED and user_info.roles is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing required role'
        )

    try:
        canonical_id = parse_uuid(user_info.sub)
    except ValueError:
        raise HTTPException(401, 'Invalid account identifier') from None
    authorization = await user_authorizer.authorize_user(
        UserProfile(
            id=canonical_id,
            email=user_info.email,
            email_verified=bool(user_info.email_verified),
            identity_provider=user_info.identity_provider,
        )
    )
    if not authorization.success:
        # For duplicate_email errors, clean up the newly created Keycloak user
        # (only if they're not already in our UserStore, i.e., they're a new user)
        if authorization.error_detail == 'duplicate_email':
            try:
                existing_user = await UserStore.get_user_by_id(user_info.sub)
                if existing_user is None:
                    from server.auth.user_management import (
                        EnterpriseUserManagementService,
                    )

                    existing_user = await EnterpriseUserManagementService().ensure_authenticated_account(
                        canonical_id, user_info.model_dump(exclude_none=True)
                    )
                if not existing_user:
                    # New user created during OAuth should be deleted from Keycloak
                    await token_manager.delete_keycloak_user(user_info.sub)
                    logger.info(
                        f'Deleted orphaned Keycloak user {user_info.sub} '
                        'after duplicate_email rejection'
                    )
            except Exception as e:
                # Log but don't fail - user should still get 401 response
                logger.warning(
                    f'Failed to clean up orphaned Keycloak user {user_info.sub}: {e}'
                )
        # Return unauthorized
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=authorization.error_detail,
        )

    email = user_info.email
    user_id = user_info.sub
    user_info_dict = user_info.model_dump(exclude_none=True)
    from server.auth.user_management import EnterpriseUserManagementService

    user = await EnterpriseUserManagementService().ensure_authenticated_account(
        canonical_id, user_info_dict
    )
    is_new_user: bool = False
    if not user:
        user = await UserStore.create_user(user_id, user_info_dict)
        is_new_user = True
    else:
        # Existing user — gradually backfill contact_name if it still has a username-style value
        await UserStore.backfill_contact_name(user_id, user_info_dict)
        await UserStore.backfill_user_email(user_id, user_info_dict)

    if not user:
        logger.error(f'Failed to authenticate user {user_info.email}')
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'Failed to authenticate user {user_info.email}',
        )

    if user.is_disabled:
        raise HTTPException(401, 'Account is unavailable')
    await IdentityRepository().link(
        canonical_id,
        'keycloak',
        f'{KEYCLOAK_SERVER_URL_EXT}/realms/{KEYCLOAK_REALM_NAME}',
        user_info.sub,
    )

    # Return leg of a post-auth git provider link: the frontend sent Keycloak
    # ``kc_action=idp_link:<provider>`` with ``link_provider`` in the state. The
    # user is already signed in, so skip the login side effects (reCAPTCHA, email
    # verification, analytics, invitations, TOS/onboarding, offline token) and
    # only store the newly linked provider's broker tokens.
    if link_provider:
        current = await get_user_auth(request)
        if await current.get_user_id() != user_id:
            raise HTTPException(
                403, 'Repository linking requires the same authenticated account'
            )
        provider = next((p for p in ProviderType if p.value == link_provider), None)
        if provider is None or provider == ProviderType.ENTERPRISE_SSO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Invalid link provider: {link_provider}',
            )

        link_status = kc_action_status or 'error'
        if link_status == 'success':
            try:
                await token_manager.store_idp_tokens(
                    provider, user_id, keycloak_access_token
                )
            except Exception:
                logger.exception(
                    'git_provider_link_store_failed',
                    extra={'user_id': user_id, 'provider': provider.value},
                    stack_info=True,
                )
                link_status = 'error'
            else:
                # Same post-login hook as below; no-op unless GitLab was linked
                schedule_gitlab_repo_sync(user_id, SecretStr(keycloak_access_token))

        logger.info(
            'git_provider_link_completed',
            extra={
                'user_id': user_id,
                'provider': provider.value,
                'status': link_status,
            },
        )
        if link_status != 'success':
            separator = '&' if '?' in redirect_url else '?'
            redirect_url = f'{redirect_url}{separator}link_status={quote(link_status)}'

        response = RedirectResponse(redirect_url, status_code=302)
        set_response_cookie(
            request=request,
            response=response,
            keycloak_access_token=keycloak_access_token,
            keycloak_refresh_token=keycloak_refresh_token,
            secure=True if web_url.startswith('https') else False,
            accepted_tos=user.accepted_tos is not None,
        )
        return response

    logger.info(f'Logging in user {str(user.id)} in org {user.current_org_id}')

    # reCAPTCHA verification with Account Defender
    if RECAPTCHA_SITE_KEY:
        if not recaptcha_token:
            logger.warning(
                'recaptcha_token_missing',
                extra={
                    'user_id': user_id,
                    'email': email,
                },
            )
            error_url = f'{web_url}/login?recaptcha_blocked=true'
            return RedirectResponse(error_url, status_code=302)

        user_ip = request.client.host if request.client else 'unknown'
        user_agent = request.headers.get('User-Agent', '')

        # Handle X-Forwarded-For for proxied requests
        forwarded_for = request.headers.get('X-Forwarded-For')
        if forwarded_for:
            user_ip = forwarded_for.split(',')[0].strip()

        try:
            result = recaptcha_service.create_assessment(
                token=recaptcha_token,
                action='LOGIN',
                user_ip=user_ip,
                user_agent=user_agent,
                email=email,
                user_id=user_id,
            )

            if not result.allowed:
                logger.warning(
                    'recaptcha_blocked_at_callback',
                    extra={
                        'user_ip': user_ip,
                        'score': result.score,
                        'user_id': user_id,
                    },
                )
                # Redirect to home with error parameter
                error_url = f'{web_url}/login?recaptcha_blocked=true'
                return RedirectResponse(error_url, status_code=302)

        except Exception:
            logger.exception(
                'reCAPTCHA verification error at callback', stack_info=True
            )
            # Fail open - continue with login if reCAPTCHA service unavailable

    # Check email verification status
    email_verified = user_info.email_verified or False
    if not email_verified:
        # Send verification email with rate limiting to prevent abuse
        # Users who repeatedly login without verifying would otherwise trigger
        # unlimited verification emails
        # Import locally to avoid circular import with email.py
        from server.routes.email import verify_email

        # Rate limit verification emails during auth flow (defaults: 60s per user,
        # 120s per IP; configurable via RATE_LIMIT_AUTH_VERIFY_EMAIL_* env vars).
        # This is separate from the manual resend limit (RATE_LIMIT_EMAIL_RESEND_*).
        rate_limited = False
        try:
            await check_rate_limit_by_user_id(
                request=request,
                key_prefix='auth_verify_email',
                user_id=user_id,
                user_rate_limit_seconds=RATE_LIMIT_AUTH_VERIFY_EMAIL_USER_SECONDS,
                ip_rate_limit_seconds=RATE_LIMIT_AUTH_VERIFY_EMAIL_IP_SECONDS,
            )
            await verify_email(request=request, user_id=user_id, is_auth_flow=True)
        except HTTPException as e:
            if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                # Rate limited - still redirect to verification page but don't send email
                rate_limited = True
                logger.info(
                    f'Rate limited verification email for user {user_id} during auth flow'
                )
            else:
                raise

        verification_redirect_url = (
            f'{web_url}/login?email_verification_required=true&user_id={user_id}'
        )
        if rate_limited:
            verification_redirect_url = f'{verification_redirect_url}&rate_limited=true'

        # Preserve invitation token so it can be included in OAuth state after verification
        if invitation_token:
            verification_redirect_url = (
                f'{verification_redirect_url}&invitation_token={invitation_token}'
            )
        response = RedirectResponse(verification_redirect_url, status_code=302)
        return response

    idp: str | None = user_info.identity_provider
    logger.info(f'Full IDP is {idp}')
    idp_type = 'oidc'
    if idp and ':' in idp:
        idp, idp_type = idp.rsplit(':', 1)
        idp_type = idp_type.lower()

    # Only fetch/store IdP tokens for OAuth-based IdPs (not SAML)
    # SAML IdPs don't have OAuth tokens to retrieve from Keycloak's broker endpoint
    if idp and idp_type != 'saml':
        await token_manager.store_idp_tokens(
            ProviderType(idp), user_id, keycloak_access_token
        )

    valid_offline_token = (
        await token_manager.validate_offline_token(user_id=user_info.sub)
        if idp and idp_type != 'saml'
        else True
    )

    logger.debug('keycloak_user_authenticated', extra={'user_id': user_id})

    if not valid_offline_token:
        param_str = urlencode(
            {
                'client_id': KEYCLOAK_CLIENT_ID,
                'response_type': 'code',
                'kc_idp_hint': idp,
                'redirect_uri': f'{web_url}/oauth/keycloak/offline/callback',
                'scope': 'openid email profile offline_access',
                'state': state,
            }
        )
        redirect_url = (
            f'{KEYCLOAK_SERVER_URL_EXT}/realms/{KEYCLOAK_REALM_NAME}/protocol/openid-connect/auth'
            f'?{param_str}'
        )

    has_accepted_tos = user.accepted_tos is not None

    user, redirect_url = await apply_login_memberships(
        user, redirect_url, invitation_token, is_new_user=is_new_user
    )
    await UserStore.record_login(user_id)

    # Server-side identity — defer to background to avoid blocking auth response
    consented = user.user_consents_to_analytics is True
    org_member_ids = [om.org_id for om in user.org_members] if user.org_members else []

    background_tasks.add_task(
        _track_login_analytics_background,
        user_id=user_id,
        email=email,
        idp=idp,
        current_org_id=user.current_org_id,
        org_member_ids=org_member_ids,
        consented=consented,
    )

    logger.info(
        'user_logged_in',
        extra={
            'idp': idp,
            'idp_type': idp_type,
            'user_id': user_id,
            'is_feature_env': IS_FEATURE_ENV,
        },
    )

    # If the user hasn't accepted the TOS, redirect to the TOS page
    if not has_accepted_tos:
        encoded_redirect_url = quote(redirect_url, safe='')
        tos_redirect_url = f'{web_url}/accept-tos?redirect_url={encoded_redirect_url}'
        if invitation_token:
            tos_redirect_url = f'{tos_redirect_url}&invitation_success=true'
        response = RedirectResponse(tos_redirect_url, status_code=302)
    else:
        # User has accepted TOS - check if they need onboarding
        # Only redirect to onboarding if user has a valid offline token,
        # otherwise they need to complete the Keycloak offline token flow first
        if valid_offline_token and await _should_redirect_to_onboarding(user_id, user):
            # Preserve the user's originally requested destination as
            # ``?returnTo=...`` so the frontend ``OnboardingForm`` can
            # restore it after the user finishes the form.
            redirect_url = _build_onboarding_redirect(redirect_url, web_url)
            logger.info(
                'Redirecting returning user to onboarding',
                extra={'user_id': user_id, 'deployment_mode': DEPLOYMENT_MODE},
            )
        if invitation_token:
            if '?' in redirect_url:
                redirect_url = f'{redirect_url}&invitation_success=true'
            else:
                redirect_url = f'{redirect_url}?invitation_success=true'
        redirect_url = _build_cross_app_redirect_url(redirect_url, web_url)
        response = RedirectResponse(redirect_url, status_code=302)

    set_response_cookie(
        request=request,
        response=response,
        keycloak_access_token=keycloak_access_token,
        keycloak_refresh_token=keycloak_refresh_token,
        secure=True if web_url.startswith('https') else False,
        accepted_tos=has_accepted_tos,
    )

    # Sync GitLab repos & set up webhooks
    # Use Keycloak access token (first-time users lack offline token at this stage)
    # Normally, offline token is used to fetch GitLab token via user_id
    schedule_gitlab_repo_sync(user_id, SecretStr(keycloak_access_token))
    return response


@oauth_router.get('/keycloak/offline/callback')
async def keycloak_offline_callback(code: str, state: str, request: Request):
    require_keycloak()
    if not code:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'error': 'Missing code in request params'},
        )

    web_url = get_web_url(request)
    redirect_uri = web_url + request.url.path

    (
        keycloak_access_token,
        keycloak_refresh_token,
    ) = await _get_keycloak_tokens_or_unavailable(code, redirect_uri)
    if not keycloak_access_token or not keycloak_refresh_token:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'error': 'Problem retrieving Keycloak tokens'},
        )

    try:
        user_info = await token_manager.get_user_info(keycloak_access_token)
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Authentication service temporarily unavailable',
            headers={'Retry-After': '1'},
        ) from exc
    # sub is a required field in KeycloakUserInfo, validation happens in get_user_info

    try:
        parse_uuid(user_info.sub)
    except ValueError:
        raise HTTPException(401, 'Invalid account identifier') from None
    user = await UserStore.get_user_by_id(user_info.sub)
    if user is None or user.is_disabled:
        raise HTTPException(401, 'Account is unavailable')
    await token_manager.store_offline_token(
        user_id=user_info.sub, offline_token=keycloak_refresh_token
    )
    redirect_url, _, _, _ = _extract_oauth_state(state)
    default_url = validate_return_destination(redirect_url, web_url)
    final_url = await _get_post_auth_redirect(user_info.sub, default_url, web_url, user)

    # Intentionally do NOT write tokens into the `keycloak_auth` cookie:
    # the cookie tracks the regular (online) session and is the token
    # passed to Keycloak's /logout endpoint. Putting the offline token
    # in the cookie causes logout to terminate the offline session.
    return RedirectResponse(final_url, status_code=302)


@oauth_router.get('/github/callback')
async def github_dummy_callback(request: Request):
    """Callback for GitHub that just forwards the user to the app base URL."""
    web_url = get_web_url(request)
    return RedirectResponse(web_url, status_code=302)


@api_router.post('/authenticate')
async def authenticate(request: Request):
    try:
        user_auth = await get_user_auth(request)
        if get_auth_mode() is AuthMode.KEYCLOAK:
            await user_auth.get_access_token()
        return JSONResponse(
            status_code=status.HTTP_200_OK, content={'message': 'User authenticated'}
        )
    except (TokenRefreshError, AuthenticationUnavailable) as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={'error': str(e) or e.__class__.__name__},
        )
    except Exception:
        # For any error during authentication, clear the auth cookie and return 401
        response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={'error': 'User is not authenticated'},
        )

        # Delete the auth cookie (and any sibling chunks) if it exists
        keycloak_auth_cookie = read_chunked_cookie(request, 'keycloak_auth')
        if keycloak_auth_cookie:
            delete_chunked_cookie(
                response,
                'keycloak_auth',
                domain=get_cookie_domain(),
                samesite=get_cookie_samesite(),
            )

        return response


@api_router.post('/accept_tos')
async def accept_tos(request: Request):
    user_auth = cast(SaasUserAuth, await get_user_auth(request))
    access_token = await user_auth.get_access_token()
    refresh_token = user_auth.refresh_token
    user_id = await user_auth.get_user_id()

    local = get_auth_mode() is AuthMode.LOCAL
    if not user_id or (not local and (not access_token or not refresh_token)):
        logger.warning(
            'accept_tos: missing authentication state',
            extra={
                'has_access_token': bool(access_token),
                'has_refresh_token': bool(refresh_token),
                'user_id': user_id,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={'error': 'User is not authenticated'},
        )

    # Get redirect URL from request body
    body = await request.json()
    web_url = get_web_url(request)
    redirect_url = body.get('redirect_url', '/' if local else str(web_url))
    if local:
        redirect_url = safe_redirect(redirect_url)
        web_url = ''
    else:
        # The offline authorization leg is a server-generated Keycloak URL.
        parsed = urlparse(redirect_url)
        keycloak_origin = urlparse(KEYCLOAK_SERVER_URL_EXT)
        if not (
            parsed.netloc == keycloak_origin.netloc
            and parsed.path
            == f'/realms/{KEYCLOAK_REALM_NAME}/protocol/openid-connect/auth'
        ):
            redirect_url = validate_return_destination(redirect_url, web_url)

    # Update user settings with TOS acceptance
    accepted_tos: datetime = datetime.now(timezone.utc).replace(tzinfo=None)
    async with a_session_maker() as session:
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        user = result.scalar_one_or_none()
        if not user:
            await session.rollback()
            logger.error('User for {user_id} not found.')
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={'error': 'User does not exist'},
            )
        user.accepted_tos = accepted_tos
        # SaaS users consent to analytics via Terms of Service acceptance
        user.user_consents_to_analytics = True
        await session.commit()

        logger.info(f'User {user_id} accepted TOS')

        # Analytics: user signed up event (fires on first TOS acceptance)
        try:
            analytics = get_analytics_service()
            if analytics:
                from openhands.analytics.analytics_context import AnalyticsContext

                org_id_str = str(user.current_org_id) if user.current_org_id else None
                email = user.email

                ctx = AnalyticsContext(
                    user_id=user_id,
                    consented=True,
                    org_id=org_id_str,
                    user=user,
                )
                analytics.track_user_signed_up(
                    ctx=ctx,
                    email_domain=email.split('@')[1]
                    if email and '@' in email
                    else None,
                )
                analytics.set_person_properties(
                    ctx=ctx,
                    properties={'signed_up_at': datetime.now(timezone.utc).isoformat()},
                )
        except Exception:
            logger.exception('analytics:user_signed_up:failed', stack_info=True)

    # Determine final redirect - but don't override if it's the offline token flow
    # (the offline callback will handle post-auth redirect after storing the token)
    is_offline_flow = not local and 'offline' in redirect_url
    if not is_offline_flow:
        redirect_url = await _get_post_auth_redirect(user_id, redirect_url, web_url)

    response = JSONResponse(
        status_code=status.HTTP_200_OK, content={'redirect_url': redirect_url}
    )

    if local:
        user_auth.accepted_tos = True
        return response
    assert access_token is not None and refresh_token is not None
    set_response_cookie(
        request=request,
        response=response,
        keycloak_access_token=access_token.get_secret_value(),
        keycloak_refresh_token=refresh_token.get_secret_value(),
        secure=True if web_url.startswith('https') else False,
        accepted_tos=True,
    )
    return response


@api_router.get('/onboarding_status')
async def onboarding_status(request: Request):
    """Return whether the current user must still complete onboarding.

    Kept as a dedicated endpoint instead of riding on ``GET /api/v1/settings``
    (the natural home for fields like ``email_verified``) because the settings
    response is heavyweight: ``SaasSettingsStore.load`` joins User, Org, and
    OrgMember rows and deep-merges the org-level and member-level
    ``agent_settings`` before returning. Onboarding gating runs on every
    protected-route navigation, so we need a lightweight read of a single
    boolean rather than paying for the full settings aggregation.
    """
    user_auth = cast(SaasUserAuth, await get_user_auth(request))
    user_id = await user_auth.get_user_id()

    if not user_id:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={'error': 'User is not authenticated'},
        )

    user = await UserStore.get_user_by_id(user_id)
    if not user:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': 'User not found'},
        )

    should_complete = await _should_redirect_to_onboarding(user_id, user)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={'should_complete_onboarding': should_complete},
    )


class OnboardingSubmission(BaseModel):
    """Payload posted from the onboarding form.

    ``selections`` maps onboarding question_id -> selected option(s), e.g.
    ``{"role": "software_engineer", "org_size": "solo",
       "use_case": ["new_features", "fixing_bugs"]}``.

    The field is optional so the endpoint stays backwards-compatible with any
    client that previously called it with an empty body, but the current
    frontend always submits a populated mapping.
    """

    selections: dict[str, str | list[str]] = {}


@api_router.post('/complete_onboarding')
async def complete_onboarding(
    request: Request, body: OnboardingSubmission | None = None
):
    """Mark onboarding as completed for the current user and fire analytics.

    Persists ``user.onboarding_completed = True`` and emits the
    ``onboarding completed`` PostHog event (plus an org ``group_identify`` to
    stamp ``onboarding_completed_at``). Analytics failures are swallowed so
    they never block the user from leaving the onboarding flow.
    """
    user_auth = cast(SaasUserAuth, await get_user_auth(request))
    user_id = await user_auth.get_user_id()

    if not user_id:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={'error': 'User is not authenticated'},
        )

    user = await UserStore.mark_onboarding_completed(user_id)
    if not user:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': 'User not found'},
        )

    logger.info(
        'User completed onboarding',
        extra={'user_id': user_id},
    )

    # Analytics: 'onboarding completed' event + org group_identify.
    # Best-effort: never let a tracking failure break the onboarding flow.
    selections = body.selections if body is not None else {}
    try:
        analytics = get_analytics_service()
        if analytics:
            ctx = await resolve_analytics_context(user_id)
            # Stamp email on the person profile so person_display_name
            # is set when the onboarding event is viewed in PostHog.
            # identify_user was a no-op at login (consent was False),
            # and accept_tos never re-triggered it, so this is the
            # first opportunity after consent is granted.
            if user.email:
                analytics.set_person_properties(
                    ctx=ctx,
                    properties={'email': user.email},
                )
            analytics.track_onboarding_completed(
                ctx=ctx,
                selections=selections,
            )
            if ctx.org_id:
                analytics.group_identify(
                    ctx=ctx,
                    group_type='org',
                    group_key=ctx.org_id,
                    properties={
                        'onboarding_completed_at': datetime.now(
                            timezone.utc
                        ).isoformat(),
                    },
                )
    except Exception:
        logger.exception('analytics:onboarding_completed:failed', stack_info=True)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={'message': 'Onboarding completed'},
    )


@api_router.post('/logout')
async def logout(request: Request):
    if get_auth_mode() is AuthMode.LOCAL:
        from server.auth.local.sessions import LocalBrowserSessionBackend

        response = JSONResponse({'message': 'User logged out'})
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            # Even a valid API-key header does not authorize revoking a cookie
            # session from a different browser principal.
            validate_csrf(request)
            await LocalBrowserSessionBackend().revoke(SecretStr(token))
        clear_session_cookie(response)
        return response
    if read_chunked_cookie(request, 'keycloak_auth'):
        validate_csrf(request)
    # Always create the response object first to ensure we can return it even if errors occur
    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={'message': 'User logged out'},
    )

    # Always delete the cookie (and any sibling chunks) regardless of what happens
    delete_chunked_cookie(
        response,
        'keycloak_auth',
        domain=get_cookie_domain(),
        samesite=get_cookie_samesite(),
    )
    response.delete_cookie(
        CSRF_COOKIE, secure=True, httponly=False, samesite='lax', path='/'
    )

    # Try to properly logout from Keycloak, but don't fail if it doesn't work.
    #
    # IMPORTANT: only terminate the Keycloak session when the resolved
    # auth is the *cookie* (browser session). ``get_user_auth`` resolves
    # bearer tokens before cookies, so a request that carries both an
    # ``Authorization: Bearer <api-key>`` header *and* a
    # ``keycloak_auth`` cookie would otherwise have its API-key-bound
    # *offline_token* terminated when the user clicked "logout" in the
    # browser. The browser intent is to drop the cookie session, not to
    # revoke a long-lived API key. The cookie itself is always deleted
    # above; we just must not nuke an offline session that belongs to a
    # different auth surface.
    try:
        user_auth = cast(SaasUserAuth, await get_user_auth(request))
        if (
            user_auth
            and user_auth.refresh_token
            and user_auth.auth_type == AuthType.COOKIE
        ):
            refresh_token = user_auth.refresh_token.get_secret_value()
            await token_manager.logout(refresh_token)
    except Exception:
        # Log any errors but don't fail the request
        logger.debug('Remote logout did not complete')
        # We still want to clear the cookie and return success

    return response


@api_router.get('/refresh-tokens', response_model=TokenResponse)
async def refresh_tokens(
    request: Request,
    provider: ProviderType,
    sid: str,
    x_session_api_key: Annotated[str | None, Header(alias='X-Session-API-Key')],
) -> TokenResponse:
    """Return the latest token for a given provider."""
    user_id = get_user_id(sid)
    session_api_key = await get_session_api_key(sid)
    if (
        not session_api_key
        or not x_session_api_key
        or session_api_key != x_session_api_key
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Forbidden')

    logger.info(f'Refreshing token for conversation {sid}')
    provider_handler = ProviderHandler(
        create_provider_tokens_object([provider]), external_auth_id=user_id
    )
    service = provider_handler.get_service(provider)
    token = await service.get_latest_token()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No token found for provider '{provider}'",
        )

    return TokenResponse(token=token.get_secret_value())
