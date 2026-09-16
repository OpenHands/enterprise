"""Browser session endpoints and completion behavior for selected authentication."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from openhands.app_server.user_auth import get_access_token
from openhands.app_server.user_auth.user_auth import AuthType, get_user_auth
from server.auth.auth_error import TokenRefreshError
from server.auth.cookie_chunking import delete_chunked_cookie, read_chunked_cookie
from server.logger import logger
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url


@dataclass(frozen=True)
class TosCompletion:
    web_url: str
    redirect_url: str
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None


class BrowserHttp(ABC):
    @abstractmethod
    async def authenticate(self, request: Request) -> JSONResponse: ...

    @abstractmethod
    async def logout(self, request: Request) -> JSONResponse: ...

    @abstractmethod
    async def prepare_tos(
        self, request: Request, redirect_url: str
    ) -> TosCompletion: ...

    @abstractmethod
    async def tos_redirect(self, user_id: str, completion: TosCompletion) -> str: ...

    @abstractmethod
    def complete_tos(
        self, request: Request, response: JSONResponse, completion: TosCompletion
    ) -> None: ...

    @abstractmethod
    async def validate_sandbox_owner(
        self, user_id: str | None, session_api_key: str | None
    ) -> None: ...


class KeycloakBrowserHttp(BrowserHttp):
    async def authenticate(self, request: Request) -> JSONResponse:
        try:
            await get_access_token(request)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={'message': 'User authenticated'},
            )
        except TokenRefreshError as e:
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

    async def logout(self, request: Request) -> JSONResponse:
        from server.auth.browser_policy import require_cookie_identity
        from server.routes.auth import token_manager

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
            user_auth = require_cookie_identity(await get_user_auth(request))
            if user_auth.refresh_token and user_auth.auth_type == AuthType.COOKIE:
                refresh_token = user_auth.refresh_token.get_secret_value()
                await token_manager.logout(refresh_token)
        except Exception as e:
            # Log any errors but don't fail the request
            logger.debug(f'Error during logout: {str(e)}')
            # We still want to clear the cookie and return success

        return response

    async def prepare_tos(self, request: Request, redirect_url: str) -> TosCompletion:
        from server.auth.browser_policy import require_cookie_identity

        identity = require_cookie_identity(await get_user_auth(request))
        access_token = await identity.get_access_token()
        if not access_token or not identity.refresh_token:
            raise HTTPException(401, 'User is not authenticated')
        return TosCompletion(
            get_web_url(request), redirect_url, access_token, identity.refresh_token
        )

    async def tos_redirect(self, user_id: str, completion: TosCompletion) -> str:
        from server.routes.auth import _get_post_auth_redirect

        if 'offline' in completion.redirect_url:
            return completion.redirect_url
        return await _get_post_auth_redirect(
            user_id, completion.redirect_url, completion.web_url
        )

    def complete_tos(
        self, request: Request, response: JSONResponse, completion: TosCompletion
    ) -> None:
        from server.routes.auth import set_response_cookie

        assert (
            completion.access_token is not None and completion.refresh_token is not None
        )
        set_response_cookie(
            request=request,
            response=response,
            keycloak_access_token=completion.access_token.get_secret_value(),
            keycloak_refresh_token=completion.refresh_token.get_secret_value(),
            secure=completion.web_url.startswith('https'),
            accepted_tos=True,
        )

    async def validate_sandbox_owner(
        self, user_id: str | None, session_api_key: str | None
    ) -> None:
        return None


class OpenHandsBrowserHttp(BrowserHttp):
    async def authenticate(self, request: Request) -> JSONResponse:
        from server.auth.composition import get_auth_services

        user_auth = await get_user_auth(request)
        user_id = await user_auth.get_user_id()
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Not authenticated')
        user = await get_auth_services().accounts.get_user_by_id(user_id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                'message': 'User authenticated',
                'accepted_tos': bool(user and user.accepted_tos is not None),
            },
        )

    async def logout(self, request: Request) -> JSONResponse:
        from server.auth.native_csrf import clear_csrf_cookie
        from server.auth.native_session import (
            SESSION_COOKIE,
            clear_api_key_cookie,
            clear_session_cookie,
        )
        from server.services.native_auth_service import get_native_auth_service

        # Revoke the actual browser session even when a separate API key
        # authenticated this request. API keys have an independent lifecycle.
        session_token = request.cookies.get(SESSION_COOKIE)
        if session_token:
            await get_native_auth_service().revoke_session(session_token)
        response = JSONResponse(content={'message': 'User logged out'})
        await clear_session_cookie(response, request)
        clear_api_key_cookie(response, request)
        clear_csrf_cookie(request, response)
        return response

    async def prepare_tos(self, request: Request, redirect_url: str) -> TosCompletion:
        from server.auth.auth_config import get_native_auth_settings
        from server.services.native_auth_service import safe_return_path

        return TosCompletion(
            get_native_auth_settings().web_url, safe_return_path(redirect_url)
        )

    async def tos_redirect(self, user_id: str, completion: TosCompletion) -> str:
        from server.routes.auth import _get_post_auth_redirect

        return await _get_post_auth_redirect(
            user_id, completion.redirect_url, completion.web_url
        )

    def complete_tos(
        self, request: Request, response: JSONResponse, completion: TosCompletion
    ) -> None:
        return None

    async def validate_sandbox_owner(
        self, user_id: str | None, session_api_key: str | None
    ) -> None:
        from server.auth.saas_user_auth import SaasUserAuth

        if not user_id or not session_api_key:
            raise HTTPException(status.HTTP_403_FORBIDDEN, 'Forbidden')
        await SaasUserAuth.get_for_user(user_id)
