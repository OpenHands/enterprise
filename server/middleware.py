import re
from typing import Callable, cast
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.auth_error import (
    AuthError,
    EmailNotVerifiedError,
    NoCredentialsError,
    TokenRefreshError,
    TosNotAcceptedError,
)
from server.auth.browser_security import (
    SESSION_COOKIE,
    clear_session_cookie,
    validate_csrf,
)
from server.auth.contracts import (
    AuthenticationUnavailable,
    InvalidCredentials,
    ProviderReconnectRequired,
)
from server.auth.cookie_chunking import delete_chunked_cookie, read_chunked_cookie
from server.auth.gitlab_sync import schedule_gitlab_repo_sync
from server.auth.saas_user_auth import SaasUserAuth
from server.routes.auth import set_response_cookie
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite


class SetAuthCookieMiddleware:
    """Apply account policy to the selected principal before endpoint effects."""

    async def __call__(self, request: Request, call_next: Callable):
        try:
            user_auth: SaasUserAuth | None
            attach = self._should_attach(request)
            if attach:
                user_auth = cast(SaasUserAuth, await get_user_auth(request))
                principal = user_auth.principal
                if (
                    principal
                    and principal.restricted
                    and request.url.path
                    not in (
                        '/api/auth/password/change',
                        '/api/logout',
                    )
                ):
                    return JSONResponse(
                        {
                            'detail': {
                                'code': 'password_change_required',
                                'redirect_url': '/auth/change-password?'
                                + urlencode({'returnTo': '/'}),
                            }
                        },
                        403,
                    )
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and getattr(
                    request.state, 'authentication_via_cookie', True
                ):
                    validate_csrf(request)
                self._check_tos(request)
                if (
                    principal
                    and principal.authentication_method == 'keycloak'
                    and not user_auth.email_verified
                    and not request.url.path.startswith('/api/email')
                    and request.url.path
                    not in (
                        '/api/settings',
                        '/api/logout',
                        '/api/authenticate',
                    )
                ):
                    raise EmailNotVerifiedError
            response: Response = await call_next(request)
            user_auth = self._get_user_auth(request)
            if (
                user_auth
                and user_auth.principal
                and user_auth.principal.authentication_method == 'keycloak'
                and user_auth.refreshed
                and user_auth.access_token is not None
                and user_auth.refresh_token is not None
                and not self._response_changes_session(response)
            ):
                set_response_cookie(
                    request=request,
                    response=response,
                    keycloak_access_token=user_auth.access_token.get_secret_value(),
                    keycloak_refresh_token=user_auth.refresh_token.get_secret_value(),
                    secure=request.url.hostname != 'localhost',
                    accepted_tos=user_auth.accepted_tos or False,
                )
                schedule_gitlab_repo_sync(user_auth.user_id)
            return response
        except HTTPException as exc:
            return JSONResponse(
                {'detail': exc.detail}, exc.status_code, headers=exc.headers
            )
        except ProviderReconnectRequired:
            return JSONResponse(
                {'detail': {'code': 'provider_reconnect_required'}}, 409
            )
        except (AuthenticationUnavailable, TokenRefreshError):
            return JSONResponse(
                {'error': 'Authentication service temporarily unavailable'}, 503
            )
        except (EmailNotVerifiedError, TosNotAcceptedError) as exc:
            return JSONResponse({'error': str(exc) or exc.__class__.__name__}, 403)
        except (InvalidCredentials, AuthError) as exc:
            # A failed request never revokes an upstream offline session. Local
            # logout and account lifecycle own explicit credential revocation.
            response = JSONResponse({'error': str(exc) or exc.__class__.__name__}, 401)
            if request.cookies.get(SESSION_COOKIE):
                clear_session_cookie(response)
            if read_chunked_cookie(request, 'keycloak_auth'):
                delete_chunked_cookie(
                    response,
                    'keycloak_auth',
                    domain=get_cookie_domain(),
                    samesite=get_cookie_samesite(),
                )
            return response

    def _get_user_auth(self, request: Request) -> SaasUserAuth | None:
        return cast(SaasUserAuth | None, getattr(request.state, 'user_auth', None))

    @staticmethod
    def _response_changes_session(response: Response) -> bool:
        # Login/logout and other auth routes own any session cookies they set
        # or delete. Background refresh must never overwrite those decisions.
        return any(
            re.fullmatch(r'keycloak_auth(?:_\d+)?|oh_session', value.split('=', 1)[0])
            is not None
            for value in response.headers.getlist('set-cookie')
        )

    def _check_tos(self, request: Request):
        if request.url.path in (
            '/api/accept_tos',
            '/api/logout',
            '/api/auth/password/change',
        ):
            return
        user_auth = self._get_user_auth(request)
        if user_auth is None:
            raise NoCredentialsError
        if (
            user_auth.principal
            and user_auth.principal.authentication_method == 'api_key'
        ):
            return
        if user_auth.accepted_tos is False:
            raise TosNotAcceptedError

    def _should_attach(self, request: Request) -> bool:
        if request.method == 'OPTIONS':
            return False
        path = request.url.path

        ignore_paths = (
            # Logout validates cookie CSRF itself and must be able to clear
            # expired credentials even when the upstream provider is offline.
            '/api/logout',
            '/api/options/config',
            '/api/auth/capabilities',
            '/api/auth/csrf',
            '/api/auth/authorize',
            '/api/auth/login',
            '/api/auth/password/forgot',
            '/api/auth/password/reset',
            '/api/auth/email/verify',
            '/api/auth/invitations/enroll',
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
            '/api/refresh-tokens',
            '/api/organizations/members/invite/accept',
            '/oauth/device/authorize',
            '/oauth/device/token',
            '/api/v1/web-client/config',
        )
        if path in ignore_paths:
            return False

        # These endpoints validate the sandbox session key and then resolve
        # a checked background principal for its owner in their dependencies.
        if request.method == 'GET' and re.fullmatch(
            r'/api/v1/sandboxes/[^/]+/settings/secrets(?:/[^/]+)?', path
        ):
            return False

        # Allow public access to shared conversations and events
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
        return is_api_route or is_mcp or path == '/oauth/device/verify-authenticated'


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
        # Cookies require strict CORS even when an invalid header may fall
        # back to that session. Preserve Origin for downstream CSRF validation.
        if any(name == b'cookie' and value for name, value in scope['headers']):
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
