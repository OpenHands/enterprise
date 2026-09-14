"""OpenHands account and browser-session request authentication."""

from types import MappingProxyType
from uuid import UUID

from fastapi import Request
from pydantic import SecretStr

from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.auth_error import (
    AuthError,
    BearerTokenError,
    CookieError,
    NoCredentialsError,
    TokenRefreshError,
)
from server.auth.native_session import SESSION_COOKIE
from server.auth.request_auth import RequestAuth, has_explicit_api_key
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import TokenManager
from server.services.native_auth_service import get_native_auth_service


class OpenHandsRequestAuth(RequestAuth):
    def api_key(self, request: Request) -> str | None:
        if 'Authorization' in request.headers:
            scheme, _, token = request.headers['Authorization'].partition(' ')
            return token if scheme.lower() == 'bearer' and token else None
        for name in ('X-Session-API-Key', 'X-Access-Token'):
            if name in request.headers:
                return request.headers[name] or None
        return request.cookies.get('api_key')

    async def from_request(self, request: Request) -> SaasUserAuth | None:
        if not has_explicit_api_key(request):
            identity = await self.from_cookie(request)
            if identity is not None:
                return identity
        identity = await self.from_bearer(request)
        if identity is None and has_explicit_api_key(request):
            raise BearerTokenError('Invalid API credential')
        return identity if identity is not None else await self.from_cookie(request)

    async def from_cookie(self, request: Request) -> SaasUserAuth | None:
        from server.auth.composition import get_auth_services

        session_token = request.cookies.get(SESSION_COOKIE)
        if not session_token:
            return None
        principal = await get_native_auth_service().authenticate_session(session_token)
        if principal is None:
            raise CookieError('Invalid or expired session')
        user = await get_auth_services().accounts.get_user_by_id(
            str(principal.account_id)
        )
        if user is None or user.is_disabled:
            raise CookieError('User account is unavailable')
        return SaasUserAuth(
            user_id=str(principal.account_id),
            email=principal.email,
            email_verified=user.email_verified,
            refresh_token=SecretStr(''),
            accepted_tos=user.accepted_tos is not None,
            credential_transport='native_cookie',
            native_session_id=principal.session_id,
            auth_time=principal.auth_time,
        )

    async def from_signed_token(self, signed_token: str) -> SaasUserAuth:
        raise CookieError('Keycloak sessions are disabled')

    async def for_user(self, user_id: str) -> SaasUserAuth:
        principal = await get_native_auth_service().get_identity(UUID(user_id))
        if principal is None:
            raise NoCredentialsError('User account is unavailable')
        return SaasUserAuth(
            user_id=str(principal.account_id),
            email=principal.email,
            refresh_token=SecretStr(''),
            auth_type=AuthType.BEARER,
            credential_transport='background',
        )

    async def for_api_key(self, user_id: str) -> SaasUserAuth | None:
        from server.auth.composition import get_auth_services

        try:
            identity = await self.for_user(user_id)
        except NoCredentialsError:
            return None
        user = await get_auth_services().accounts.get_user_by_id(user_id)
        identity.email_verified = user.email_verified if user else None
        return identity

    async def for_integration(
        self, user_id: str, manager: TokenManager
    ) -> SaasUserAuth:
        return await self.for_user(user_id)

    async def get_latest_provider_token(
        self, identity: SaasUserAuth, provider: ProviderType
    ) -> str | None:
        return None

    def database_error(self) -> AuthError:
        return TokenRefreshError('Authentication temporarily unavailable')

    async def refresh(self, identity: SaasUserAuth) -> None:
        raise RuntimeError('Keycloak token refresh is disabled')

    def token_expired(self, identity: SaasUserAuth, token: SecretStr) -> bool:
        raise RuntimeError('Keycloak tokens are disabled')

    async def get_access_token(self, identity: SaasUserAuth) -> SecretStr | None:
        return None

    async def get_provider_tokens(self, identity: SaasUserAuth) -> PROVIDER_TOKEN_TYPE:
        return MappingProxyType({})
