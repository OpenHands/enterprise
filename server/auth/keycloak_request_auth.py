"""Keycloak request credentials and token state; caches remain on the identity."""

from __future__ import annotations

import time
from types import MappingProxyType
from uuid import UUID

import jwt
from fastapi import Request
from keycloak.exceptions import KeycloakConnectionError
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import delete, select
from tenacity import RetryError

from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderToken,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user_auth.user_auth import AuthType, UserAuth
from server.auth.auth_error import (
    AuthError,
    BearerTokenError,
    CookieError,
    ExpiredError,
    TokenRefreshError,
)
from server.auth.constants import AZURE_DEVOPS_ORGANIZATION, BITBUCKET_DATA_CENTER_HOST
from server.auth.cookie_chunking import read_chunked_cookie
from server.auth.request_auth import RequestAuth
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import TokenManager
from server.logger import logger
from storage.auth_tokens import AuthTokens
from storage.database import a_session_maker
from storage.user_authorization import UserAuthorizationType
from storage.user_authorization_store import UserAuthorizationStore


class _ExpiryClaims(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    sub: str
    exp: int | None = None


class _TokenClaims(_ExpiryClaims):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    sub: str
    email: str | None = None
    email_verified: bool | None = None
    exp: int | None = None


class _SessionTokens(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    access_token: str
    refresh_token: str
    accepted_tos: bool | None = None


def _is_transient_keycloak_error(exc: BaseException) -> bool:
    # The retry wrapper is the third-party exception boundary.
    while isinstance(exc, RetryError):
        retry_exc = exc.last_attempt.exception()
        if retry_exc is None:
            return False
        exc = retry_exc
    return isinstance(exc, KeycloakConnectionError)


class KeycloakRequestAuth(RequestAuth):
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
        from server.auth.saas_user_auth import (
            saas_user_auth_from_bearer,
            saas_user_auth_from_cookie,
        )

        identity = await saas_user_auth_from_bearer(request)
        return (
            identity
            if identity is not None
            else await saas_user_auth_from_cookie(request)
        )

    async def for_user(self, user_id: str) -> SaasUserAuth:
        return SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr(''),
            auth_type=AuthType.BEARER,
            credential_transport='background',
        )

    async def for_api_key(self, user_id: str) -> SaasUserAuth | None:
        return await self.for_user(user_id)

    async def for_integration(
        self, user_id: str, manager: TokenManager
    ) -> SaasUserAuth:
        offline_token = await manager.load_offline_token(user_id)
        if offline_token is None:
            logger.info('no_offline_token_found')
        return SaasUserAuth(
            user_id=user_id, refresh_token=SecretStr(offline_token or '')
        )

    async def get_latest_provider_token(
        self, identity: SaasUserAuth, provider: ProviderType
    ) -> str | None:
        return await UserAuth.get_latest_provider_token(identity, provider)

    def database_error(self) -> AuthError:
        return BearerTokenError()

    async def from_cookie(self, request: Request) -> SaasUserAuth | None:
        from server.auth.saas_user_auth import saas_user_auth_from_signed_token

        try:
            signed_token = read_chunked_cookie(request, 'keycloak_auth')
            if not signed_token:
                return None
            return await saas_user_auth_from_signed_token(signed_token)
        except Exception as exc:
            raise CookieError from exc

    async def from_signed_token(self, signed_token: str) -> SaasUserAuth:
        from server.auth.composition import get_auth_services
        from storage.encrypt_utils import get_jwt_service

        decoded = _SessionTokens.model_validate(
            get_jwt_service().verify_jws_token(signed_token)
        )
        claims = _TokenClaims.model_validate(
            jwt.decode(decoded.access_token, options={'verify_signature': False})
        )
        try:
            UUID(claims.sub)
        except ValueError:
            user = None
        else:
            user = await get_auth_services().accounts.get_user_by_id(claims.sub)
            if user is None:
                raise AuthError('Access denied: user account not found')
        if user is not None and user.is_disabled:
            raise AuthError('Access denied: user account is disabled')
        if claims.email:
            authorization = await UserAuthorizationStore.get_authorization_type(
                claims.email, None
            )
            if authorization == UserAuthorizationType.BLACKLIST:
                raise AuthError(
                    'Access denied: Your email domain is not allowed to access this service'
                )
        return SaasUserAuth(
            access_token=SecretStr(decoded.access_token),
            refresh_token=SecretStr(decoded.refresh_token),
            user_id=claims.sub,
            email=claims.email,
            email_verified=claims.email_verified,
            accepted_tos=decoded.accepted_tos,
            auth_type=AuthType.COOKIE,
        )

    async def refresh(self, identity: SaasUserAuth) -> None:
        from server.auth import saas_user_auth

        # API-key (bearer) auth does not carry an offline token. Load it lazily
        # here, and only when a Keycloak access token is genuinely needed, so
        # that authentication itself never depends on the offline session.
        if (
            identity.auth_type == AuthType.BEARER
            and not identity.refresh_token.get_secret_value()
        ):
            offline_token = await saas_user_auth.token_manager.load_offline_token(
                identity.user_id
            )
            if not offline_token:
                raise ExpiredError()
            identity.refresh_token = SecretStr(offline_token)

        if identity._is_token_expired(identity.refresh_token):
            logger.debug('saas_user_auth_refresh:expired')
            raise ExpiredError()

        tokens = _SessionTokens.model_validate(
            await saas_user_auth.token_manager.refresh(
                identity.refresh_token.get_secret_value()
            )
        )
        identity.access_token = SecretStr(tokens.access_token)
        identity.refresh_token = SecretStr(tokens.refresh_token)
        identity.refreshed = True
        if not identity.email or not identity.email_verified or not identity.user_id:
            # We don't need to verify the signature here because we just refreshed
            # this token from the IDP via saas_user_auth.token_manager.refresh()
            access_token_payload = _TokenClaims.model_validate(
                jwt.decode(tokens.access_token, options={'verify_signature': False})
            )
            identity.user_id = access_token_payload.sub
            identity.email = access_token_payload.email
            identity.email_verified = access_token_payload.email_verified

    def token_expired(self, identity: SaasUserAuth, token: SecretStr) -> bool:
        logger.debug('saas_user_auth_is_token_expired')
        # Decode token payload - works with both access and refresh tokens
        payload = _ExpiryClaims.model_validate(
            jwt.decode(token.get_secret_value(), options={'verify_signature': False})
        )

        # Sanity check - make sure we refer to current user
        assert payload.sub == identity.user_id

        # Check token expiration
        expiration = payload.exp
        if expiration:
            logger.debug('saas_user_auth_is_token_expired expiration is %d', expiration)
        return expiration is not None and expiration < time.time()

    async def get_access_token(self, identity: SaasUserAuth) -> SecretStr | None:
        logger.debug('saas_user_auth_get_access_token')
        try:
            if identity.access_token is None or identity._is_token_expired(
                identity.access_token
            ):
                await identity.refresh()
            return identity.access_token
        except AuthError:
            # A Keycloak access token requires the user's offline session. For
            # API-key (bearer) auth that session is optional: provider tokens
            # and the rest of the request context resolve independently, so a
            # missing/revoked offline session must not turn a valid key into a
            # 401. Degrade to None instead of raising.
            if identity.auth_type == AuthType.BEARER:
                logger.warning('bearer_get_access_token_refresh_failed', exc_info=True)
                return None
            raise
        except Exception as e:
            if identity.auth_type == AuthType.BEARER:
                logger.warning('bearer_get_access_token_failed', exc_info=True)
                return None
            if _is_transient_keycloak_error(e):
                raise TokenRefreshError(
                    'Authentication service temporarily unavailable'
                ) from e
            raise AuthError() from e

    async def get_provider_tokens(
        self, identity: SaasUserAuth
    ) -> PROVIDER_TOKEN_TYPE | None:
        from server.auth import saas_user_auth

        logger.debug('saas_user_auth_get_provider_tokens')
        if identity.provider_tokens is not None:
            return identity.provider_tokens
        provider_tokens: dict[ProviderType, ProviderToken] = {}

        user_secrets = await identity.get_secrets()

        try:
            # TODO: I think we can do this in a single request if we refactor
            async with a_session_maker() as session:
                result = await session.execute(
                    select(AuthTokens).where(
                        AuthTokens.keycloak_user_id == identity.user_id
                    )
                )
                tokens = result.scalars().all()

            for token in tokens:
                idp_type = ProviderType(token.identity_provider)
                if idp_type == ProviderType.ENTERPRISE_SSO:
                    # enterprise_sso is a login-only IdP, not a git provider:
                    # ProviderHandler has no service for it and its tokens
                    # cannot be refreshed (the row would be deleted and the
                    # request 401'd). Skip rows minted by older logins.
                    continue
                try:
                    host = None
                    if user_secrets and idp_type in user_secrets.provider_tokens:
                        host = user_secrets.provider_tokens[idp_type].host

                    if idp_type == ProviderType.BITBUCKET_DATA_CENTER and not host:
                        host = BITBUCKET_DATA_CENTER_HOST or None

                    if idp_type == ProviderType.AZURE_DEVOPS and not host:
                        host = AZURE_DEVOPS_ORGANIZATION or None

                    # Resolve the provider token by user_id directly. This reads
                    # the encrypted token from the auth_tokens table and refreshes
                    # via the provider's OAuth endpoint — no Keycloak access
                    # token / offline session required.
                    provider_token = (
                        await saas_user_auth.token_manager.get_idp_token_by_user_id(
                            identity.user_id,
                            idp=idp_type,
                        )
                    )
                    # TODO: Currently we don't store the IDP user id in our refresh table. We should.
                    provider_tokens[idp_type] = ProviderToken(
                        token=SecretStr(provider_token), user_id=None, host=host
                    )
                except Exception:
                    # If there was a problem with a refresh token we log and delete it
                    logger.exception(
                        'Error refreshing provider_token token',
                        extra={
                            'user_id': identity.user_id,
                            'idp_type': token.identity_provider,
                        },
                        stack_info=True,
                    )
                    async with a_session_maker() as session:
                        await session.execute(
                            delete(AuthTokens).where(AuthTokens.id == token.id)
                        )
                        await session.commit()
                    raise

            identity.provider_tokens = MappingProxyType(provider_tokens)
            return identity.provider_tokens
        except Exception as e:
            # Any error refreshing tokens means we need to log in again
            raise AuthError() from e
