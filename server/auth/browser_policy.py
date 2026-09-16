import re
from collections.abc import Awaitable
from typing import Callable, Protocol, runtime_checkable
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openhands.app_server.user_auth.user_auth import AuthType, UserAuth, get_user_auth
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_error import (
    AuthError,
    EmailNotVerifiedError,
    NoCredentialsError,
    TokenRefreshError,
    TosNotAcceptedError,
)
from server.auth.browser_http import (
    BrowserHttp,
    KeycloakBrowserHttp,
    OpenHandsBrowserHttp,
)
from server.auth.cookie_chunking import delete_chunked_cookie, read_chunked_cookie
from server.auth.gitlab_sync import schedule_gitlab_repo_sync
from server.auth.saas_user_auth import token_manager
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite


@runtime_checkable
class NativeRequestIdentity(Protocol):
    @property
    def credential_transport(self) -> str: ...

    @property
    def accepted_tos(self) -> bool | None: ...


@runtime_checkable
class KeycloakRequestIdentity(Protocol):
    user_id: str
    email: str | None
    email_verified: bool | None
    accepted_tos: bool | None
    auth_type: AuthType
    refreshed: bool
    access_token: SecretStr | None
    refresh_token: SecretStr

    async def get_user_id(self) -> str | None: ...
    async def get_access_token(self) -> SecretStr | None: ...
    async def refresh(self) -> None: ...


def require_cookie_identity(identity: UserAuth) -> KeycloakRequestIdentity:
    # UserAuth is an operator-selected extension point. Validate its cookie
    # contract without requiring a particular implementation or subclass.
    if not isinstance(identity, KeycloakRequestIdentity):
        raise AuthError('Authentication adapter lacks enterprise cookie state')
    return identity


class BrowserPolicy(BrowserHttp):
    """Selected browser transport including identity, cookies, CSRF and origins."""

    def cors_app(self, app: ASGIApp, *, permissive: bool) -> ASGIApp:
        raise NotImplementedError

    async def __call__(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        raise NotImplementedError

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
        )
        if path in ignore_paths:
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
        return is_api_route or is_mcp


class KeycloakBrowserPolicy(KeycloakBrowserHttp, BrowserPolicy):
    def cors_app(self, app: ASGIApp, *, permissive: bool) -> ASGIApp:
        return _OriginStrippingApp(app) if permissive else app

    async def __call__(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        from server.routes.auth import set_response_cookie

        keycloak_auth_cookie = read_chunked_cookie(request, 'keycloak_auth')
        logger.debug('request_with_cookie', extra={'cookie': keycloak_auth_cookie})
        try:
            if self._should_attach(request):
                self._check_tos(request)

            response: Response = await call_next(request)
            if not keycloak_auth_cookie:
                return response
            user_auth = self._get_user_auth(request)
            if not user_auth or user_auth.auth_type != AuthType.COOKIE:
                return response
            if user_auth.refreshed:
                if user_auth.access_token is None:
                    return response
                set_response_cookie(
                    request=request,
                    response=response,
                    keycloak_access_token=user_auth.access_token.get_secret_value(),
                    keycloak_refresh_token=user_auth.refresh_token.get_secret_value(),
                    secure=False if request.url.hostname == 'localhost' else True,
                    accepted_tos=user_auth.accepted_tos or False,
                )

                # On re-authentication (token refresh), kick off background sync for GitLab repos
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
            # Only attempt a Keycloak logout when this looked like a cookie
            # session going bad. Bearer-token auth failures (e.g., a
            # ``BearerTokenError`` from a transient Keycloak refresh
            # failure) must NOT revoke the user's offline session — that
            # would brick every subsequent API-key call until the user
            # logs back in through the browser. The API key's lifecycle is
            # managed via key mint/delete, not via per-request refresh
            # outcomes. See ``_logout`` for the defense-in-depth check.
            if keycloak_auth_cookie:
                try:
                    await self._logout(request)
                except Exception as logout_error:
                    logger.debug(str(logout_error))

            # Send a response that deletes the auth cookie if needed
            response = JSONResponse(
                {'error': str(e) or e.__class__.__name__}, status.HTTP_401_UNAUTHORIZED
            )
            if keycloak_auth_cookie:
                delete_chunked_cookie(
                    response,
                    'keycloak_auth',
                    domain=get_cookie_domain(),
                    samesite=get_cookie_samesite(),
                )
            return response

    def _get_user_auth(self, request: Request) -> KeycloakRequestIdentity | None:
        user_auth: UserAuth | None = getattr(request.state, 'user_auth', None)
        if user_auth is None:
            return None
        return require_cookie_identity(user_auth)

    def _check_tos(self, request: Request) -> None:
        keycloak_auth_cookie = read_chunked_cookie(request, 'keycloak_auth')
        auth_header = request.headers.get('Authorization')
        mcp_auth_header = request.headers.get('X-Session-API-Key')
        api_auth_header = request.headers.get('X-Access-Token')
        api_key_cookie = request.cookies.get('api_key')
        accepted_tos: bool | None = False
        if (
            keycloak_auth_cookie is None
            and (auth_header is None or not auth_header.startswith('Bearer '))
            and mcp_auth_header is None
            and api_auth_header is None
            and api_key_cookie is None
        ):
            raise NoCredentialsError

        if keycloak_auth_cookie:
            try:
                from storage.encrypt_utils import get_jwt_service

                decoded = get_jwt_service().verify_jws_token(keycloak_auth_cookie)
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

    async def _logout(self, request: Request) -> None:
        # Log out of keycloak - this prevents issues where you did not log in with the idp you believe you used.
        #
        # IMPORTANT: only terminate the Keycloak session when the request
        # carried a *cookie* (browser session). For bearer-token (API
        # key) requests, ``user_auth.refresh_token`` is the user's stored
        # *offline_token* loaded from ``OfflineTokenStore``. Calling
        # ``token_manager.logout`` with that value asks Keycloak to
        # revoke the offline session, which permanently breaks every API
        # key minted for the user until they re-authenticate through the
        # browser (``/keycloak/callback`` rewrites the offline_token).
        # A single transient Keycloak hiccup that surfaces as
        # ``BearerTokenError`` must not be allowed to cause this damage.
        try:
            user_auth = require_cookie_identity(await get_user_auth(request))
            if (
                user_auth
                and user_auth.refresh_token
                and user_auth.auth_type == AuthType.COOKIE
            ):
                await token_manager.logout(user_auth.refresh_token.get_secret_value())
        except Exception:
            logger.debug('Error logging out')


class OpenHandsBrowserPolicy(OpenHandsBrowserHttp, BrowserPolicy):
    def cors_app(self, app: ASGIApp, *, permissive: bool) -> ASGIApp:
        return _NativeCorsApp(app)

    def _should_attach(self, request: Request) -> bool:
        if request.method == 'OPTIONS':
            return False
        path = request.url.path
        if path.startswith('/integration/'):
            return not path.endswith('/events')
        if path in (
            '/api/organizations/members/invite/accept',
            '/oauth/device/verify-authenticated',
        ) and request.method not in ('GET', 'HEAD'):
            return True
        return super()._should_attach(request)

    async def __call__(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Authenticate and protect browser mutations without legacy token handling."""
        from server.auth.native_csrf import validate_csrf
        from server.auth.native_password import NativeAuthError
        from server.auth.native_session import SESSION_COOKIE
        from server.services.native_auth_service import get_native_auth_service

        if request.url.path.startswith(('/oauth/keycloak/', '/api/keycloak/')):
            return JSONResponse({'detail': 'Not found'}, status.HTTP_404_NOT_FOUND)

        public_auth_paths = {
            '/api/auth/csrf',
            '/api/auth/password/login',
            '/api/auth/password/reset/complete',
            '/api/auth/enrollment/inspect',
            '/api/auth/enrollment/complete',
        }
        path = request.url.path
        protected = self._should_attach(request) and path not in public_auth_paths
        if (
            request.method == 'GET'
            and 'X-Session-API-Key' in request.headers
            and 'Authorization' not in request.headers
            and 'X-Access-Token' not in request.headers
            and (
                path == '/api/refresh-tokens'
                or re.fullmatch(
                    r'/api/v1/sandboxes/[^/]+/settings/secrets(?:/[^/]+)?', path
                )
            )
        ):
            # These read-only routes validate the sandbox credential themselves,
            # including running state/ownership, and resolve its native owner.
            # A sandbox key must not become general application authentication.
            protected = False
        session_token = request.cookies.get(SESSION_COOKIE)
        user_auth: NativeRequestIdentity | None = None
        try:
            if protected:
                configured_identity = await get_user_auth(request)
                # Configured adapters may be independent native implementations.
                # Validate the required interface at this dynamic integration boundary.
                if not isinstance(configured_identity, NativeRequestIdentity):
                    raise AuthError(
                        'Authentication adapter lacks application identity state'
                    )
                user_auth = configured_identity

            mutation = request.method not in ('GET', 'HEAD', 'OPTIONS')
            if mutation and (protected or path in public_auth_paths):
                # Header presence is insufficient: provenance comes only from
                # a credential that was validated by SaasUserAuth.
                non_cookie_auth = bool(
                    user_auth and user_auth.credential_transport == 'bearer'
                )
                requires_browser_proof = (
                    path == '/api/auth/password/change'
                    or (path == '/api/logout' and session_token is not None)
                    or (path.startswith('/integration/') and session_token is not None)
                    or (
                        path.startswith('/api/admin/auth-accounts/')
                        and path.endswith('/password-reset')
                    )
                )
                if not non_cookie_auth or requires_browser_proof:
                    if not _trusted_native_origin(request):
                        raise HTTPException(403, 'Untrusted request origin')
                    if (
                        session_token
                        and (
                            user_auth is None
                            or user_auth.credential_transport != 'native_cookie'
                        )
                        and await get_native_auth_service().authenticate_session(
                            session_token
                        )
                        is None
                    ):
                        # Public auth and bearer-authenticated browser actions
                        # must also reject a revoked or expired browser session.
                        raise HTTPException(403, 'Invalid CSRF token')
                    await validate_csrf(request)

            if mutation and path == '/api/v1/secrets/git-providers':
                raise HTTPException(
                    409, 'Git provider connections are unavailable without Keycloak'
                )

            if (
                user_auth
                and user_auth.credential_transport == 'native_cookie'
                and user_auth.accepted_tos is False
                and path
                not in {
                    '/api/accept_tos',
                    '/api/authenticate',
                    '/api/logout',
                    '/api/v1/users/me',
                    '/api/auth/enrollment/accept-membership',
                }
            ):
                raise TosNotAcceptedError
            return await call_next(request)
        except HTTPException as exc:
            return JSONResponse(
                {'detail': exc.detail}, exc.status_code, headers=exc.headers
            )
        except NativeAuthError as exc:
            return JSONResponse({'error': str(exc)}, exc.status_code)
        except (SQLAlchemyError, TokenRefreshError):
            return JSONResponse(
                {'error': 'Authentication temporarily unavailable'},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except AuthError as exc:
            # Do not log raw credentials or invoke Keycloak on this path.
            return JSONResponse(
                {'error': str(exc) or exc.__class__.__name__},
                status.HTTP_401_UNAUTHORIZED,
            )


def _trusted_native_origin(request: Request) -> bool:
    """Check the original browser origin against operator-configured origins."""
    from server.config import get_native_cors_origins

    origin = request.headers.get('Origin')
    try:
        if origin is None:
            referer = request.headers.get('Referer')
            if not referer:
                return False
            parsed = urlsplit(referer)
            origin = f'{parsed.scheme}://{parsed.netloc}'
        parsed = urlsplit(origin)
        parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in ('http', 'https')
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ('', '/')
        or parsed.query
        or parsed.fragment
    ):
        return False
    return origin.rstrip('/') in get_native_cors_origins()


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


class _NativeCorsApp:
    """Retain Origin for CSRF while making the outer CORS policy authoritative."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def filtered_send(message: Message) -> None:
            if message['type'] == 'http.response.start':
                message = dict(message)
                message['headers'] = [
                    (name, value)
                    for name, value in message.get('headers', [])
                    if not name.lower().startswith(b'access-control-')
                ]
            await send(message)

        await self.app(scope, receive, filtered_send)
