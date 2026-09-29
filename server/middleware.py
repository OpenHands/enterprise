from typing import Callable, cast

import jwt
from fastapi import Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openhands.app_server.user_auth.user_auth import AuthType, UserAuth, get_user_auth
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_error import (
    AuthError,
    EmailNotVerifiedError,
    NoCredentialsError,
    TokenRefreshError,
    TosNotAcceptedError,
)
from server.auth.cookie_chunking import delete_chunked_cookie, read_chunked_cookie
from server.auth.gitlab_sync import schedule_gitlab_repo_sync
from server.auth.saas_user_auth import SaasUserAuth, token_manager
from server.routes.auth import set_response_cookie
from server.routes.oauth_v2 import _set_oauth_v2_cookie
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite

# The small JWT cookie set by the OAuth v2 login flow (Phase 2).
OAUTH_V2_COOKIE_NAME = 'openhands_auth'


class SetAuthCookieMiddleware:
    """
    Dual-cookie auth middleware (Phase 2).

    Reads both the new ``openhands_auth`` JWT cookie (new logins) and the old
    ``keycloak_auth`` chunked cookie (existing sessions). New logins produce
    the JWT cookie via the v2 routes; old sessions keep working until their
    cookies naturally expire. When an IDP token refreshes, the JWT cookie is
    re-minted (the old cookie is untouched). No forced re-login.
    """

    async def __call__(self, request: Request, call_next: Callable):
        v2_cookie = request.cookies.get(OAUTH_V2_COOKIE_NAME)
        keycloak_auth_cookie = read_chunked_cookie(request, 'keycloak_auth')
        logger.debug(
            'request_with_cookie',
            extra={
                'openhands_auth': bool(v2_cookie),
                'keycloak_auth': bool(keycloak_auth_cookie),
            },
        )
        try:
            if self._should_attach(request):
                self._check_tos(request)

            response: Response = await call_next(request)
            user_auth = self._get_user_auth(request)
            if not user_auth or user_auth.auth_type != AuthType.COOKIE:
                return response

            # OAuth v2 cookie: re-mint the small JWT cookie when the IDP
            # access token was refreshed. The old ``keycloak_auth`` cookie is
            # never touched on this path.
            if user_auth.oauth_v2_cookie and user_auth.refreshed:
                self._remint_v2_cookie(request, response, user_auth)
                # On re-authentication (token refresh), kick off background
                # sync for GitLab repos, mirroring the legacy path.
                user_id = await user_auth.get_user_id()
                if user_id:
                    schedule_gitlab_repo_sync(user_id)
                return response

            # Legacy ``keycloak_auth`` cookie path: re-set the chunked cookie
            # only if a refresh happened and the cookie was present.
            if (
                keycloak_auth_cookie
                and user_auth.refreshed
                and user_auth.access_token is not None
            ):
                set_response_cookie(
                    request=request,
                    response=response,
                    keycloak_access_token=user_auth.access_token.get_secret_value(),
                    keycloak_refresh_token=user_auth.refresh_token.get_secret_value(),
                    secure=False if request.url.hostname == 'localhost' else True,
                    accepted_tos=user_auth.accepted_tos or False,
                )
                user_id = await user_auth.get_user_id()
                if user_id:
                    schedule_gitlab_repo_sync(user_id)

            if (
                self._should_attach(request)
                and not request.url.path.startswith('/api/email')
                and request.url.path
                not in ('/api/settings', '/api/logout', '/api/authenticate')
                and not user_auth.email_verified
            ):
                raise EmailNotVerifiedError

            return response
        except EmailNotVerifiedError as e:
            return JSONResponse(
                {'error': str(e) or e.__class__.__name__}, status.HTTP_403_FORBIDDEN
            )
        except NoCredentialsError as e:
            logger.info(e.__class__.__name__)
            # The user is trying to use an expired token or has not logged in. No special event handling is required
            return JSONResponse(
                {'error': str(e) or e.__class__.__name__}, status.HTTP_401_UNAUTHORIZED
            )
        except TokenRefreshError as e:
            logger.warning('auth_service_unavailable', exc_info=True)
            return JSONResponse(
                {'error': str(e) or e.__class__.__name__},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except AuthError as e:
            logger.warning('auth_error', exc_info=True)
            # Only attempt a Keycloak logout when this looked like a legacy
            # cookie session going bad. Bearer-token auth failures and v2
            # cookie failures must not revoke the user's offline session.
            if keycloak_auth_cookie:
                try:
                    await self._logout(request)
                except Exception as logout_error:
                    logger.debug(str(logout_error))

            # Send a response that deletes the auth cookie(s) if present.
            response = JSONResponse(
                {'error': str(e) or e.__class__.__name__}, status.HTTP_401_UNAUTHORIZED
            )
            if v2_cookie:
                response.delete_cookie(
                    OAUTH_V2_COOKIE_NAME,
                    domain=get_cookie_domain(),
                    samesite=get_cookie_samesite(),
                )
            if keycloak_auth_cookie:
                delete_chunked_cookie(
                    response,
                    'keycloak_auth',
                    domain=get_cookie_domain(),
                    samesite=get_cookie_samesite(),
                )
            return response

    def _remint_v2_cookie(
        self, request: Request, response: Response, user_auth: SaasUserAuth
    ) -> None:
        """Re-mint the ``openhands_auth`` JWT cookie after an IDP refresh."""
        user_id = user_auth.user_id
        if not user_id:
            return
        _set_oauth_v2_cookie(
            request=request,
            response=response,
            user_id=user_id,
            access_token_expires_at=user_auth.access_token_expires_at,
            accepted_tos=bool(user_auth.accepted_tos),
            refresh_token_expires_at=user_auth.idp_refresh_token_expires_at,
        )

    def _get_user_auth(self, request: Request) -> SaasUserAuth | None:
        user_auth: UserAuth | None = getattr(request.state, 'user_auth', None)
        if user_auth is None:
            return None
        return cast(SaasUserAuth, user_auth)

    def _check_tos(self, request: Request):
        v2_cookie = request.cookies.get(OAUTH_V2_COOKIE_NAME)
        keycloak_auth_cookie = read_chunked_cookie(request, 'keycloak_auth')
        auth_header = request.headers.get('Authorization')
        mcp_auth_header = request.headers.get('X-Session-API-Key')
        api_auth_header = request.headers.get('X-Access-Token')
        api_key_cookie = request.cookies.get('api_key')
        accepted_tos: bool | None = False
        if (
            v2_cookie is None
            and keycloak_auth_cookie is None
            and (auth_header is None or not auth_header.startswith('Bearer '))
            and mcp_auth_header is None
            and api_auth_header is None
            and api_key_cookie is None
        ):
            raise NoCredentialsError

        if v2_cookie or keycloak_auth_cookie:
            try:
                from storage.encrypt_utils import get_jwt_service

                signed = v2_cookie or keycloak_auth_cookie
                decoded = get_jwt_service().verify_jws_token(signed)
                accepted_tos = decoded.get('accepted_tos')
            except (jwt.InvalidTokenError, ValueError):
                logger.warning('Invalid JWT signature detected')
                raise AuthError('Invalid authentication token')
            except Exception as e:
                logger.warning(f'JWT decode error: {str(e)}')
                raise AuthError('Invalid authentication token') from e
        else:
            # Don't fail an API call if the TOS has not been accepted.
            # The user will accept the TOS the next time they login.
            accepted_tos = True

        # Reject only an explicit False (shown the TOS, declined). Users who
        # have not re-logged in since the last TOS change (accepted_tos is
        # None) are not logged out.
        if accepted_tos is False and request.url.path != '/api/accept_tos':
            logger.warning('User has not accepted the terms of service')
            raise TosNotAcceptedError

    def _should_attach(self, request: Request) -> bool:
        if request.method == 'OPTIONS':
            return False
        path = request.url.path

        ignore_paths = (
            '/api/options/config',
            '/api/keycloak/callback',
            '/api/billing/success',
            '/api/billing/cancel',
            '/api/billing/customer-setup-success',
            '/api/billing/stripe-webhook',
            '/api/email/resend',
            # Quota-increase verification is opened from the user's work
            # email client, often without an app session; the signed JWS
            # token in the query string is the credential.
            '/api/quota/verify',
            '/api/organizations/members/invite/accept',
            '/oauth/device/authorize',
            '/oauth/device/token',
            '/api/v1/web-client/config',
            # OAuth providers redirect the user's browser here after an MCP
            # server install consent; the cross-site navigation carries no
            # session cookie and the route validates its single-use state.
            '/api/v1/mcp/oauth/callback',
        )
        if path in ignore_paths:
            return False

        # Shared conversations and events: authentication is optional there
        # (see server/sharing), so the middleware never blocks them.
        if path.startswith('/api/shared-conversations') or path.startswith(
            '/api/shared-events'
        ):
            return False

        # Webhooks access is controlled using separate API keys
        if path.startswith('/api/v1/webhooks/'):
            return False

        # Service API uses its own authentication (X-Service-API-Key header)
        if path.startswith('/api/service/'):
            return False

        is_mcp = path.startswith('/mcp')
        is_api_route = path.startswith('/api')
        return is_api_route or is_mcp

    async def _logout(self, request: Request):
        # Log out of keycloak - this prevents issues where you did not log in with the idp you believe you used.
        #
        # IMPORTANT: only terminate the Keycloak session when the request
        # carried a legacy ``keycloak_auth`` *cookie* (browser session). For
        # bearer-token (API key) requests, ``user_auth.refresh_token`` is the
        # user's stored *offline_token* loaded from ``OfflineTokenStore``.
        # Calling ``token_manager.logout`` with that value asks Keycloak to
        # revoke the offline session, which permanently breaks every API key
        # minted for the user until they re-authenticate through the browser
        # (``/keycloak/callback`` rewrites the offline_token). A single
        # transient Keycloak hiccup that surfaces as ``BearerTokenError`` must
        # not be allowed to cause this damage. OAuth v2 cookie sessions
        # (``openhands_auth``) have no Keycloak session to terminate — their
        # IDP session is managed by the IDP directly — so they are skipped too.
        try:
            user_auth = cast(SaasUserAuth, await get_user_auth(request))
            if (
                user_auth
                and user_auth.refresh_token
                and user_auth.auth_type == AuthType.COOKIE
                and not getattr(user_auth, 'oauth_v2_cookie', False)
            ):
                await token_manager.logout(user_auth.refresh_token.get_secret_value())
        except Exception:
            logger.debug('Error logging out')


_CREDENTIALLESS_PATH_PREFIXES = (
    # RFC 8628 device authorization endpoints — unauthenticated by design,
    # called cross-origin from clients that are exchanging device codes for
    # API keys.
    '/oauth/device/authorize',
    '/oauth/device/token',
)


class _OriginStrippingApp:
    """Hide Origin from inner middleware after outer CORS classifies the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        inner_scope = dict(scope)
        inner_scope['headers'] = tuple(
            (name, value)
            for name, value in scope['headers']
            if name.lower() != b'origin'
        )
        await self.app(inner_scope, receive, send)


class ApiKeyAwareCORSMiddleware:
    """CORS dispatcher that loosens the policy for credential-less requests.

    Requests that authenticate via API key (``Authorization: Bearer …``,
    ``X-Session-API-Key``, or ``X-Access-Token``) or that target a known
    unauthenticated cross-origin endpoint (RFC 8628 device flow) get
    ``Access-Control-Allow-Origin: *`` with credentials disabled — the
    wildcard is safe because the browser cannot attach cookies when
    credentials are off, so the only way to authenticate is the explicit
    key (or no auth, for public endpoints).

    Cookie/session requests keep the strict origin allowlist with
    credentials enabled.
    """

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        self._permissive = CORSMiddleware(
            _OriginStrippingApp(app),
            allow_origins=['*'],
            allow_credentials=False,
            allow_methods=['*'],
            allow_headers=['*'],
        )
        self._strict = CORSMiddleware(
            app,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and self._is_credentialless(scope):
            await self._permissive(scope, receive, send)
        else:
            await self._strict(scope, receive, send)

    @staticmethod
    def _is_credentialless(scope: Scope) -> bool:
        path = scope.get('path', '')
        if any(path.startswith(prefix) for prefix in _CREDENTIALLESS_PATH_PREFIXES):
            return True
        if scope['method'] == 'OPTIONS':
            # Preflight: the auth header hasn't been sent yet, so look at the
            # headers the browser is asking permission to send. Parse the
            # comma-separated list into a set so we match whole header names
            # only — otherwise something like ``x-my-authorization-token``
            # would substring-match ``authorization``.
            for name, value in scope['headers']:
                if name == b'access-control-request-headers':
                    requested_headers = {
                        h.strip() for h in value.decode('latin-1').lower().split(',')
                    }
                    return bool(
                        requested_headers
                        & {'authorization', 'x-session-api-key', 'x-access-token'}
                    )
            return False
        for name, value in scope['headers']:
            if name == b'authorization' and value[:7].lower() == b'bearer ':
                return True
            if name in (b'x-session-api-key', b'x-access-token'):
                return True
        return False


class PostHogSessionMiddleware:
    """Extract the PostHog session ID from the incoming request header.

    Stores the value on ``request.state.posthog_session_id`` so that
    subsequent event-capture call sites can link server-side events to the
    corresponding frontend session-replay recording.

    When the ``X-POSTHOG-SESSION-ID`` header is absent the attribute is set
    to ``None`` — never raises, never blocks.
    """

    async def __call__(self, request: Request, call_next: Callable):
        request.state.posthog_session_id = request.headers.get('X-POSTHOG-SESSION-ID')
        return await call_next(request)
