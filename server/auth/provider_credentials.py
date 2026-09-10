"""Encrypted Git credentials owned by an authenticated OpenHands account.

Provider identity is established by the provider's authenticated user endpoint.
Neither email addresses nor user IDs supplied in a connection request are proof
of ownership. Legacy broker acquisition is isolated in provider_compatibility.
"""

import os
import time
from types import MappingProxyType
from typing import NoReturn
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.integrations.azure_devops.azure_devops_service import (
    AzureDevOpsService,
)
from openhands.app_server.integrations.bitbucket.bitbucket_service import (
    BitBucketService,
)
from openhands.app_server.integrations.bitbucket_data_center.bitbucket_dc_service import (
    BitbucketDCService,
)
from openhands.app_server.integrations.forgejo.forgejo_service import ForgejoService
from openhands.app_server.integrations.github.github_service import GitHubService
from openhands.app_server.integrations.gitlab.constants import GITLAB_HOST
from openhands.app_server.integrations.gitlab.gitlab_service import GitLabService
from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderToken,
    ProviderType,
)
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    GitService,
)
from openhands.app_server.integrations.service_types import User as ProviderUser
from openhands.app_server.services.jwt_service import JwtService
from server.auth.constants import AZURE_DEVOPS_ORGANIZATION, BITBUCKET_DATA_CENTER_HOST
from server.auth.contracts import (
    AuthenticationUnavailable,
    InvalidCredentials,
    ProviderReconnectRequired,
)
from server.auth.provider_token_refresh import ProviderTokenRefresher
from storage.auth_tokens import AuthTokens
from storage.database import a_session_maker
from storage.user import User


def _normalize_domain(host: str) -> str:
    parsed = urlsplit(host if '://' in host else f'https://{host}')
    if (
        parsed.scheme != 'https'
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ('', '/')
        or '\\' in host
    ):
        raise ValueError('Provider host must be an HTTPS hostname without a path')
    # Accessing port also rejects malformed port values.
    port = parsed.port
    domain = parsed.hostname.encode('idna').decode().lower().rstrip('.')
    if ':' in domain:
        domain = f'[{domain}]'
    return f'{domain}:{port}' if port and port != 443 else domain


def normalize_provider_host(provider: ProviderType, host: str | None = None) -> str:
    """Limit token delivery to the provider or its configured Enterprise host."""
    if provider == ProviderType.AZURE_DEVOPS:
        value = (host or AZURE_DEVOPS_ORGANIZATION).strip().rstrip('/')
        if value.startswith('https://dev.azure.com/'):
            value = value.removeprefix('https://dev.azure.com/')
        elif value.startswith('dev.azure.com/'):
            value = value.removeprefix('dev.azure.com/')
        if not value or not all(c.isalnum() or c in '-_' for c in value):
            raise ValueError('Azure DevOps requires an organization name')
        # An organization is a path component on dev.azure.com, never a host.
        return value.lower()
    defaults = {
        ProviderType.GITHUB: 'github.com',
        ProviderType.GITLAB: GITLAB_HOST,
        ProviderType.BITBUCKET: 'bitbucket.org',
        ProviderType.BITBUCKET_DATA_CENTER: BITBUCKET_DATA_CENTER_HOST,
        ProviderType.FORGEJO: urlsplit(
            os.getenv('FORGEJO_BASE_URL', 'https://codeberg.org/api/v1')
        ).netloc,
    }
    configured = defaults.get(provider)
    if not configured:
        raise ValueError(f'Provider {provider.value} is not configured')
    allowed = _normalize_domain(configured)
    normalized = _normalize_domain(host) if host else allowed
    if normalized != allowed:
        raise ValueError(f'Host is not configured for provider {provider.value}')
    return normalized


class ProviderCredentialService:
    def __init__(self, jwt_service: JwtService | None = None):
        if jwt_service is None:
            from storage.encrypt_utils import get_jwt_service

            jwt_service = get_jwt_service()
        self._jwt_svc = jwt_service
        self._refresher = ProviderTokenRefresher(jwt_service)

    async def _validate_token(
        self, provider: ProviderType, token: SecretStr, host: str
    ) -> ProviderUser:
        services: dict[ProviderType, type[GitService]] = {
            ProviderType.GITHUB: GitHubService,
            ProviderType.GITLAB: GitLabService,
            ProviderType.BITBUCKET: BitBucketService,
            ProviderType.BITBUCKET_DATA_CENTER: BitbucketDCService,
            ProviderType.AZURE_DEVOPS: AzureDevOpsService,
            ProviderType.FORGEJO: ForgejoService,
        }
        # Use concrete base services: validation must not invoke SaaS refresh or
        # an actor lookup using a user ID asserted by the client.
        service = services[provider](token=token, base_domain=host)
        try:
            if isinstance(service, BitbucketDCService):
                user = await self._validate_bitbucket_dc(service)
            else:
                user = await service.get_user()
        except AuthenticationError as exc:
            cause = exc.__cause__
            if isinstance(cause, httpx.HTTPStatusError):
                self._raise_provider_error(cause)
            if isinstance(cause, httpx.RequestError):
                raise AuthenticationUnavailable(
                    'Git provider is temporarily unavailable'
                ) from exc
            raise ProviderReconnectRequired(
                'Invalid provider token. Reconnect the provider.'
            ) from exc
        except httpx.HTTPStatusError as exc:
            self._raise_provider_error(exc)
        except Exception as exc:
            raise AuthenticationUnavailable(
                'Git provider is temporarily unavailable'
            ) from exc
        if not user.id:
            raise ProviderReconnectRequired(
                'Token must identify a provider user account'
            )
        return user

    @staticmethod
    async def _validate_bitbucket_dc(service: BitbucketDCService) -> ProviderUser:
        """Resolve the authenticated principal, refusing fuzzy user matches.

        The general-purpose BBDC get_user helper tolerates anonymous/repo tokens
        and unavailable whoami endpoints. A credential association requires an
        exact account and must preserve network errors instead of treating them
        as an anonymous token.
        """
        base_url = service.BASE_URL.rsplit('/rest/api/1.0', 1)[0]
        try:
            whoami, _ = await service._make_request(
                f'{base_url}/plugins/servlet/applinks/whoami'
            )
        except Exception as exc:
            cause = exc.__cause__ or exc
            if (
                not isinstance(cause, httpx.HTTPStatusError)
                or cause.response.status_code != 404
            ):
                raise
            whoami = None
        username = whoami.strip() if isinstance(whoami, str) else ''
        if not username:
            _, headers = await service._make_request(
                f'{service.BASE_URL}/projects', {'limit': '0'}
            )
            username = unquote(
                next(
                    (
                        value
                        for key, value in headers.items()
                        if key.lower() == 'x-ausername'
                    ),
                    '',
                )
            )
        if not username or username.lower() == 'anonymous':
            raise AuthenticationError('Token does not identify a provider account')
        data, _ = await service._make_request(
            f'{service.BASE_URL}/users', {'filter': username}
        )
        matches = [
            user
            for user in data.get('values', [])
            if any(
                str(user.get(key, '')).casefold() == username.casefold()
                for key in ('name', 'slug')
            )
        ]
        if len(matches) != 1 or matches[0].get('id') is None:
            raise AuthenticationError(
                'Provider account could not be identified uniquely'
            )
        return ProviderUser(id=str(matches[0]['id']), login=username, avatar_url='')

    @staticmethod
    def _raise_provider_error(exc: httpx.HTTPStatusError) -> NoReturn:
        if exc.response.status_code == 401:
            raise ProviderReconnectRequired(
                'Provider token is invalid or revoked. Reconnect the provider.'
            ) from exc
        if exc.response.status_code == 400:
            try:
                payload = exc.response.json()
                error = payload.get('error') if isinstance(payload, dict) else None
            except ValueError:
                error = None
            if error in ('invalid_grant', 'invalid_token', 'bad_refresh_token'):
                raise ProviderReconnectRequired(
                    'Provider credentials have expired. Reconnect the provider.'
                ) from exc
        raise AuthenticationUnavailable(
            'Git provider is temporarily unavailable'
        ) from exc

    async def connect(
        self,
        user_id: UUID | str,
        provider: ProviderType,
        token: SecretStr,
        host: str | None = None,
    ) -> ProviderToken:
        normalized_host = normalize_provider_host(provider, host)
        if not token.get_secret_value():
            raise ProviderReconnectRequired('A provider access token is required')
        # Check local account state before making a provider API request.
        async with a_session_maker() as session:
            user = await session.get(User, UUID(str(user_id)))
            if user is None or user.is_disabled:
                raise InvalidCredentials('Account is unavailable')
        account = await self._validate_token(provider, token, normalized_host)
        try:
            async with a_session_maker() as session, session.begin():
                # Serialize replacement per account, including the first token.
                user = await session.scalar(
                    select(User).where(User.id == UUID(str(user_id))).with_for_update()
                )
                if user is None or user.is_disabled:
                    raise InvalidCredentials('Account is unavailable')
                row = await session.scalar(
                    select(AuthTokens).where(
                        AuthTokens.keycloak_user_id == str(user_id),
                        AuthTokens.identity_provider == provider.value,
                    )
                )
                if row is None:
                    row = AuthTokens(
                        keycloak_user_id=str(user_id), identity_provider=provider.value
                    )
                    session.add(row)
                row.access_token = self._jwt_svc.encrypt_value(token.get_secret_value())
                row.refresh_token = None
                row.access_token_expires_at = None
                row.refresh_token_expires_at = None
                row.credential_kind = 'manual'
                row.provider_account_id = str(account.id)
                row.provider_host = normalized_host
        except IntegrityError as exc:
            raise ProviderReconnectRequired(
                'This provider account is already connected to another OpenHands account'
            ) from exc
        return ProviderToken(token=token, user_id=str(account.id), host=normalized_host)

    def _as_token(self, row: AuthTokens) -> ProviderToken:
        return ProviderToken(
            token=SecretStr(self._jwt_svc.decrypt_value(row.access_token)),
            user_id=row.provider_account_id,
            host=row.provider_host
            or normalize_provider_host(ProviderType(row.identity_provider)),
        )

    @staticmethod
    async def _require_account(session: AsyncSession, user_id: UUID | str) -> User:
        user = await session.get(User, UUID(str(user_id)))
        if user is None or user.is_disabled:
            raise InvalidCredentials('Account is unavailable')
        return user

    async def stored_tokens(self, user_id: UUID | str) -> PROVIDER_TOKEN_TYPE:
        """Load secrets without refresh so reconnect/disconnect works during outages."""
        async with a_session_maker() as session:
            rows = (
                await session.scalars(
                    select(AuthTokens).where(
                        AuthTokens.keycloak_user_id == str(user_id),
                        AuthTokens.identity_provider
                        != ProviderType.ENTERPRISE_SSO.value,
                    )
                )
            ).all()
            if rows:
                await self._require_account(session, user_id)
            return MappingProxyType(
                {
                    ProviderType(row.identity_provider): self._as_token(row)
                    for row in rows
                }
            )

    async def get_token(
        self, user_id: UUID | str, provider: ProviderType, host: str | None = None
    ) -> ProviderToken:
        query = select(AuthTokens).where(
            AuthTokens.keycloak_user_id == str(user_id),
            AuthTokens.identity_provider == provider.value,
        )
        try:
            async with a_session_maker() as session, session.begin():
                await self._require_account(session, user_id)
                if (
                    session.bind is not None
                    and session.bind.dialect.name == 'postgresql'
                ):
                    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                row = await session.scalar(query.with_for_update())
                if row is None or provider == ProviderType.ENTERPRISE_SSO:
                    raise ProviderReconnectRequired(
                        f'Connect {provider.value} in Settings > Integrations'
                    )
                if host and normalize_provider_host(provider, host) != (
                    row.provider_host or normalize_provider_host(provider)
                ):
                    raise ProviderReconnectRequired(
                        'Provider credential belongs to a different host'
                    )
                if (
                    row.credential_kind == 'oauth'
                    and row.access_token_expires_at
                    and row.access_token_expires_at < int(time.time()) + 900
                ):
                    if not row.refresh_token:
                        raise ProviderReconnectRequired(
                            'Provider access token expired. Reconnect the provider.'
                        )
                    try:
                        refreshed = await self._refresher._check_expiration_and_refresh(
                            provider,
                            row.refresh_token,
                            row.access_token_expires_at,
                            row.refresh_token_expires_at or 0,
                        )
                    except (ValueError, KeyError, TypeError) as exc:
                        raise AuthenticationUnavailable(
                            'Provider token refresh is temporarily unavailable'
                        ) from exc
                    if refreshed:
                        row.access_token = str(refreshed['access_token'])
                        row.refresh_token = str(refreshed['refresh_token'])
                        row.access_token_expires_at = int(
                            refreshed['access_token_expires_at']
                        )
                        row.refresh_token_expires_at = int(
                            refreshed['refresh_token_expires_at']
                        )
                return self._as_token(row)
        except httpx.HTTPStatusError as exc:
            self._raise_provider_error(exc)
        except (httpx.RequestError, OperationalError) as exc:
            raise AuthenticationUnavailable(
                'Provider credentials are temporarily unavailable'
            ) from exc

    async def list_tokens(self, user_id: UUID | str) -> PROVIDER_TOKEN_TYPE:
        tokens = await self.stored_tokens(user_id)
        return MappingProxyType(
            {provider: await self.get_token(user_id, provider) for provider in tokens}
        )

    async def disconnect(self, user_id: UUID | str, provider: ProviderType) -> None:
        if provider == ProviderType.ENTERPRISE_SSO:
            raise ValueError('Enterprise SSO is not a Git provider')
        from server.auth.provider_compatibility import unlink_broker_provider

        await unlink_broker_provider(str(user_id), provider)
        async with a_session_maker() as session, session.begin():
            await session.execute(
                delete(AuthTokens).where(
                    AuthTokens.keycloak_user_id == str(user_id),
                    AuthTokens.identity_provider == provider.value,
                )
            )

    async def resolve_user(
        self, provider: ProviderType, account_id: str, host: str | None = None
    ) -> UUID | None:
        normalized_host = normalize_provider_host(provider, host)
        async with a_session_maker() as session:
            user_id = await session.scalar(
                select(AuthTokens.keycloak_user_id).where(
                    AuthTokens.identity_provider == provider.value,
                    AuthTokens.provider_host == normalized_host,
                    AuthTokens.provider_account_id == str(account_id),
                )
            )
        if user_id:
            async with a_session_maker() as session:
                await self._require_account(session, user_id)
            return UUID(user_id)
        from server.auth.provider_compatibility import resolve_broker_actor

        resolved = await resolve_broker_actor(provider, str(account_id))
        if resolved:
            async with a_session_maker() as session:
                await self._require_account(session, resolved)
        return resolved

    async def token_for_actor(
        self, provider: ProviderType, account_id: str, host: str | None = None
    ) -> SecretStr | None:
        user_id = await self.resolve_user(provider, account_id, host)
        if user_id is None:
            return None
        return (await self.get_token(user_id, provider, host)).token

    async def token_for_service(
        self,
        provider: ProviderType,
        *,
        user_id: str | None,
        account_id: str | None,
        access_token: SecretStr | None,
        host: str | None = None,
    ) -> SecretStr | None:
        if user_id:
            return (await self.get_token(user_id, provider, host)).token
        if account_id:
            return await self.token_for_actor(provider, account_id, host)
        if access_token:
            from server.auth.provider_compatibility import user_from_broker_token

            user_id = await user_from_broker_token(access_token)
            return (await self.get_token(user_id, provider, host)).token
        return None
