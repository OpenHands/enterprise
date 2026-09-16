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
    CookieError,
    NoCredentialsError,
    TokenRefreshError,
)
from server.auth.cookie_chunking import read_chunked_cookie
from server.auth.request_auth import RequestAuth
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import TokenManager
from server.services.native_auth_service import get_native_auth_service


class OpenHandsRequestAuth(RequestAuth):
    def api_key(self, request: Request) -> str | None:
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            return auth_header.replace('Bearer ', '')
        for name in ('X-Session-API-Key', 'X-Access-Token'):
            value = request.headers.get(name)
            if value:
                return value
        return request.cookies.get('api_key')

    async def from_request(self, request: Request) -> SaasUserAuth | None:
        identity = await self.from_bearer(request)
        return identity if identity is not None else await self.from_cookie(request)

    async def from_cookie(self, request: Request) -> SaasUserAuth | None:
        signed_token = read_chunked_cookie(request, 'keycloak_auth')
        if not signed_token:
            return None
        return await self.from_signed_token(signed_token)

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
