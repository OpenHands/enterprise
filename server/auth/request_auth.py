"""Request authentication contract, independent of installation selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import Request
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.app_server.integrations.service_types import ProviderType
from server.auth.auth_error import AuthError, BearerTokenError
from server.auth.token_manager import TokenManager
from storage.api_key_store import ApiKeyStore

if TYPE_CHECKING:
    from server.auth.saas_user_auth import SaasUserAuth


def has_explicit_api_key(request: Request) -> bool:
    return any(
        name in request.headers
        for name in ('Authorization', 'X-Session-API-Key', 'X-Access-Token')
    )


class RequestAuth(ABC):
    """Stateless selected policy; mutable identity and tokens belong to a request."""

    @abstractmethod
    def api_key(self, request: Request) -> str | None: ...

    @abstractmethod
    async def from_request(self, request: Request) -> SaasUserAuth | None: ...

    @abstractmethod
    async def from_cookie(self, request: Request) -> SaasUserAuth | None: ...

    @abstractmethod
    async def from_signed_token(self, signed_token: str) -> SaasUserAuth: ...

    @abstractmethod
    async def for_user(self, user_id: str) -> SaasUserAuth: ...

    @abstractmethod
    async def for_api_key(self, user_id: str) -> SaasUserAuth | None: ...

    @abstractmethod
    async def for_integration(
        self, user_id: str, manager: TokenManager
    ) -> SaasUserAuth: ...

    @abstractmethod
    async def get_latest_provider_token(
        self, identity: SaasUserAuth, provider: ProviderType
    ) -> str | None: ...

    @abstractmethod
    def database_error(self) -> AuthError: ...

    @abstractmethod
    async def refresh(self, identity: SaasUserAuth) -> None: ...

    @abstractmethod
    def token_expired(self, identity: SaasUserAuth, token: SecretStr) -> bool: ...

    @abstractmethod
    async def get_access_token(self, identity: SaasUserAuth) -> SecretStr | None: ...

    @abstractmethod
    async def get_provider_tokens(
        self, identity: SaasUserAuth
    ) -> PROVIDER_TOKEN_TYPE | None: ...

    async def from_bearer(self, request: Request) -> SaasUserAuth | None:
        from server.auth.composition import get_auth_services

        try:
            api_key = self.api_key(request)
            if not api_key:
                return None
            validation = await ApiKeyStore.get_instance().validate_api_key(api_key)
            if validation is None:
                return None
            identity = await self.for_api_key(validation.user_id)
            if identity is None:
                return None
            try:
                UUID(validation.user_id)
            except ValueError:
                user = None
            else:
                user = await get_auth_services().accounts.get_user_by_id(
                    validation.user_id
                )
                if user is None:
                    return None
            if user is not None and user.is_disabled:
                return None
            identity.api_key_org_id = validation.org_id
            identity.api_key_id = validation.key_id
            identity.api_key_name = validation.key_name
            identity.credential_transport = (
                'bearer' if has_explicit_api_key(request) else 'api_key_cookie'
            )
            return identity
        except SQLAlchemyError as exc:
            raise self.database_error() from exc
        except Exception as exc:
            raise BearerTokenError from exc
