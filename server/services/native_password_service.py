"""Password updates and administrator-issued FastAPI Users recovery links."""

from datetime import timedelta
from functools import lru_cache
from uuid import UUID

from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import exceptions, schemas

from server.auth.native_password import NativeAuthError
from server.auth.native_types import PasswordResetLink, SessionFactory
from server.auth.password_users import PasswordUserManager
from server.services.native_account_service import require_active_admin
from server.services.native_auth_service import NativeAuthService, _now


class NativePasswordService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.auth = NativeAuthService(session_factory)
        self.sessions = self.auth.sessions

    async def issue_password_reset(
        self, creator_id: UUID, account_id: UUID
    ) -> PasswordResetLink:
        async with self.sessions() as session:
            await require_active_admin(session, creator_id)
            manager = PasswordUserManager(session)
            try:
                user = await manager.get(account_id)
                await manager.forgot_password(user)
            except (exceptions.UserNotExists, exceptions.UserInactive) as exc:
                raise NativeAuthError('Account is unavailable', 404) from exc
            if manager.reset_token is None:
                raise NativeAuthError('Password reset is unavailable', 503)
            from server.auth.auth_config import get_native_auth_settings

            return {
                'reset_url': f'{get_native_auth_settings().web_url}/password-reset#token={manager.reset_token}',
                'expires_at': _now()
                + timedelta(seconds=manager.reset_password_token_lifetime_seconds),
            }

    async def complete_password_reset(
        self, token: str, new_password: str, *, client_ip: str
    ) -> None:
        await self.auth.throttle('password_reset', client_ip)
        async with self.sessions() as session:
            manager = PasswordUserManager(session)
            try:
                await manager.reset_password(token, new_password)
            except (
                exceptions.InvalidResetPasswordToken,
                exceptions.UserNotExists,
                exceptions.UserInactive,
            ) as exc:
                raise NativeAuthError('Invalid or expired password reset link') from exc

    async def change_password(
        self,
        account_id: UUID,
        session_token: str,
        current_password: str,
        new_password: str,
        *,
        client_ip: str,
    ) -> None:
        await self.auth.throttle('password_change', client_ip, str(account_id))
        principal = await self.auth.authenticate_session(session_token)
        if principal is None or principal.account_id != account_id:
            raise NativeAuthError('Not authenticated', 401)
        async with self.sessions() as session:
            manager = PasswordUserManager(session)
            user = await manager.authenticate(
                OAuth2PasswordRequestForm(
                    username=principal.email, password=current_password
                )
            )
            if user is None or user.id != account_id or not user.is_active:
                raise NativeAuthError('Invalid current password', 401)
            await manager.update(
                schemas.BaseUserUpdate(password=new_password), user, safe=True
            )

    async def recover_password(self, account_id: UUID, new_password: str) -> None:
        async with self.sessions() as session:
            manager = PasswordUserManager(session)
            try:
                user = await manager.get(account_id)
                await manager.update(
                    schemas.BaseUserUpdate(password=new_password), user, safe=True
                )
            except exceptions.UserNotExists as exc:
                raise NativeAuthError('Account not found', 404) from exc


@lru_cache(maxsize=1)
def get_native_password_service() -> NativePasswordService:
    return NativePasswordService()
