"""Application-owned authentication boundaries.

Provider SDK objects and FastAPI Users models must stay inside their adapters.
Account IDs always refer to the existing OpenHands ``user`` table.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import SecretStr

from openhands.app_server.integrations.provider import ProviderToken
from openhands.app_server.integrations.service_types import ProviderType


class InvalidCredentials(Exception):
    """The supplied account credentials are not valid."""


class SessionExpired(InvalidCredentials):
    """The browser session has expired or has been revoked."""


class AuthenticationUnavailable(Exception):
    """Authentication could not complete because a dependency is unavailable."""


class ProviderReconnectRequired(Exception):
    """A repository connection needs new credentials; the login remains valid."""


AuthenticationMethod = Literal['password', 'keycloak', 'api_key', 'background']


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    authentication_method: AuthenticationMethod
    authenticated_at: datetime
    # Internal identifiers only: never put a bearer token or cookie value here.
    session_id: str | None = None
    api_key_id: str | None = None
    organization_id: UUID | None = None
    restricted: bool = False


@dataclass(frozen=True)
class UserProfile:
    id: UUID
    email: str | None
    email_verified: bool = False
    is_disabled: bool = False
    role_id: int | None = None
    identity_provider: str | None = None


@dataclass(frozen=True)
class RequestCredentials:
    bearer_token: SecretStr | None = None
    cookies: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class IssuedSession:
    principal: Principal
    token: SecretStr
    expires_at: datetime


class AuthenticationService(Protocol):
    async def authenticate(self, credentials: RequestCredentials) -> Principal:
        """Authenticate a request and enforce current account access policy."""
        ...


class BrowserSessionBackend(Protocol):
    async def issue(
        self, user_id: UUID, *, restricted: bool = False
    ) -> IssuedSession: ...

    async def validate(self, token: SecretStr) -> Principal: ...

    async def revoke(self, token: SecretStr) -> None: ...

    async def revoke_all(self, user_id: UUID) -> None: ...


class PasswordCredentialService(Protocol):
    async def verify(self, email: str, password: SecretStr) -> UserProfile: ...

    async def establish(
        self,
        user_id: UUID,
        email: str,
        password: SecretStr,
        *,
        must_change_password: bool,
    ) -> None: ...

    async def change(
        self, user_id: UUID, current_password: SecretStr, new_password: SecretStr
    ) -> None: ...

    async def reset(self, user_id: UUID, new_password: SecretStr) -> None:
        """Called only after an authorized recovery or consumed reset token."""
        ...


@dataclass(frozen=True)
class AuthActionEmail:
    email: str
    purpose: str
    token: SecretStr = field(repr=False)


class UserManagementService(Protocol):
    async def get(self, user_id: UUID) -> UserProfile | None: ...

    async def search(self, email: str) -> Sequence[UserProfile]: ...

    async def create(
        self,
        email: str,
        password: SecretStr,
        *,
        actor: Principal,
        organization_id: UUID | None = None,
    ) -> UserProfile: ...

    async def request_email_change(
        self, user_id: UUID, email: str, *, actor: Principal
    ) -> AuthActionEmail | None:
        """Issue verification; completion consumes proof and updates the account atomically."""
        ...

    async def disable(self, user_id: UUID, *, actor: Principal) -> None: ...

    async def delete(self, user_id: UUID, *, actor: Principal) -> None: ...


class IdentityRepository(Protocol):
    async def resolve(
        self, connection: str, issuer: str, subject: str
    ) -> UUID | None: ...

    async def link(
        self, user_id: UUID, connection: str, issuer: str, subject: str
    ) -> None:
        """Record a proven identity; never infer ownership from matching email."""
        ...


class ProviderCredentialService(Protocol):
    async def connect(
        self,
        user_id: UUID,
        provider: ProviderType,
        token: SecretStr,
        host: str | None = None,
    ) -> ProviderToken: ...

    async def get_token(
        self, user_id: UUID, provider: ProviderType, host: str | None = None
    ) -> ProviderToken: ...

    async def list_tokens(
        self, user_id: UUID
    ) -> Mapping[ProviderType, ProviderToken]: ...

    async def disconnect(self, user_id: UUID, provider: ProviderType) -> None: ...

    async def resolve_user(
        self, provider: ProviderType, account_id: str, host: str | None = None
    ) -> UUID | None: ...
