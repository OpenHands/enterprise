"""Resolve Enterprise request credentials into the common account context."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import Request
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from server.auth.admission import check_account_admission
from server.auth.browser_security import SESSION_COOKIE
from server.auth.contracts import (
    AuthenticationUnavailable,
    InvalidCredentials,
    Principal,
    RequestCredentials,
)
from server.auth.mode import AuthMode, get_auth_mode
from storage.api_key_store import ApiKeyStore
from storage.user import User
from storage.user_store import UserStore

if TYPE_CHECKING:
    from server.auth.saas_user_auth import SaasUserAuth


@dataclass
class AuthenticationResult:
    principal: Principal
    user: User
    via_cookie: bool
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    accepted_tos: bool | None = None
    api_key_name: str | None = None
    refreshed: bool = False


async def active_account(user_id: UUID) -> User:
    # Only an authenticated Keycloak-era identity may trigger legacy hydration.
    user = await UserStore.get_user_by_id(str(user_id))
    if get_auth_mode() is AuthMode.KEYCLOAK and (
        user is None or user.email is None or user.email_verified is None
    ):
        from server.auth.user_management import EnterpriseUserManagementService

        user = await EnterpriseUserManagementService().ensure_authenticated_account(
            user_id
        )
    if user is None or user.is_disabled:
        raise InvalidCredentials('Account is unavailable')
    await check_account_admission(user)
    return user


class AuthenticationService:
    async def authenticate(self, credentials: RequestCredentials) -> Principal:
        return (await self.resolve(credentials)).principal

    async def resolve(self, credentials: RequestCredentials) -> AuthenticationResult:
        mode = get_auth_mode()
        try:
            api_key = credentials.bearer_token
            via_cookie = api_key is None
            if api_key is None and credentials.cookies.get('api_key'):
                api_key = SecretStr(credentials.cookies['api_key'])
            if api_key:
                validated = await ApiKeyStore.get_instance().validate_api_key(
                    api_key.get_secret_value()
                )
                if validated:
                    try:
                        user_id = UUID(validated.user_id)
                    except (ValueError, TypeError, AttributeError):
                        raise InvalidCredentials('Invalid account identifier') from None
                    user = await active_account(user_id)
                    return AuthenticationResult(
                        principal=Principal(
                            user_id=user_id,
                            authentication_method='api_key',
                            authenticated_at=datetime.now(timezone.utc),
                            api_key_id=str(validated.key_id),
                            organization_id=validated.org_id,
                        ),
                        user=user,
                        via_cookie=via_cookie,
                        accepted_tos=user.accepted_tos is not None,
                        api_key_name=validated.key_name,
                    )
            if mode is AuthMode.LOCAL:
                from server.auth.local.sessions import LocalBrowserSessionBackend

                token = credentials.cookies.get(SESSION_COOKIE)
                if not token:
                    raise InvalidCredentials('Authentication required')
                principal = await LocalBrowserSessionBackend().validate(
                    SecretStr(token)
                )
                user = await active_account(principal.user_id)
                return AuthenticationResult(
                    principal=principal,
                    user=user,
                    via_cookie=True,
                    accepted_tos=user.accepted_tos is not None,
                )
            from server.auth.keycloak.browser import authenticate_cookie

            return await authenticate_cookie(credentials.cookies)
        except SQLAlchemyError:
            raise AuthenticationUnavailable(
                'Authentication is temporarily unavailable'
            ) from None

    async def authenticate_request(self, request: Request) -> 'SaasUserAuth':
        from openhands.app_server.user_auth.user_auth import AuthType
        from server.auth.saas_user_auth import SaasUserAuth

        # The API key cookie is handled separately so CSRF exemptions reflect the
        # credential actually selected, including invalid-header cookie fallback.
        api_key: str | None
        header = request.headers.get('Authorization', '')
        if header.startswith('Bearer '):
            api_key = header[7:]
        else:
            api_key = request.headers.get('X-Session-API-Key') or request.headers.get(
                'X-Access-Token'
            )
        result = await self.resolve(
            RequestCredentials(
                bearer_token=SecretStr(api_key) if api_key else None,
                cookies=request.cookies,
            )
        )
        principal = result.principal
        request.state.authentication_via_cookie = result.via_cookie
        instance = SaasUserAuth(
            user_id=str(principal.user_id),
            principal=principal,
            email=result.user.email,
            email_verified=result.user.email_verified,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            refreshed=result.refreshed,
            accepted_tos=result.accepted_tos,
            auth_type=AuthType.BEARER
            if principal.authentication_method == 'api_key' and not result.via_cookie
            else AuthType.COOKIE,
            api_key_id=int(principal.api_key_id) if principal.api_key_id else None,
            api_key_org_id=principal.organization_id
            if principal.authentication_method == 'api_key'
            else None,
            api_key_name=result.api_key_name,
            _x_org_id_header=request.headers.get('X-Org-Id'),
        )
        # Resolve org header conflicts immediately, before any endpoint side effects.
        organization_id = await instance.get_effective_org_id()
        instance.principal = replace(principal, organization_id=organization_id)
        return instance
