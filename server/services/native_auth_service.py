"""Local identity and opaque browser-session security primitives."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import quote, unquote
from uuid import UUID

from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.native_password import (
    NativeAuthError,
    normalize_email,
)
from server.auth.native_session import digest_token
from server.auth.native_types import (
    NativeProfileMetadata,
    SessionFactory,
)
from server.auth.password_users import PasswordUser, PasswordUserManager
from server.constants import DEPLOYMENT_MODE
from server.services.native_account_service import create_profile, lock_native_lifecycle
from storage.database import a_session_maker
from storage.native_auth import (
    AuthAccount,
    AuthThrottle,
    BrowserSession,
    PasswordCredential,
)
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User


@dataclass(frozen=True)
class NativePrincipal:
    account_id: UUID
    email: str
    session_id: UUID | None = None
    auth_time: datetime | None = None
    auth_method: str | None = None


@dataclass(frozen=True)
class NativeLogin:
    principal: NativePrincipal
    token: str = field(repr=False)
    redirect_to: str = '/'


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_token(token: str | None) -> bool:
    return bool(token and 32 <= len(token) <= 128 and token.isascii())


def safe_return_path(path: str | None) -> str:
    if not path or len(path) > 2048:
        return '/'
    decoded = unquote(path)
    if (
        not decoded.startswith('/')
        or decoded.startswith('//')
        or '\\' in decoded
        or any(ord(c) < 32 for c in decoded)
    ):
        return '/'
    return path


async def _redirect(
    session: AsyncSession, user: User, return_path: str | None = None
) -> str:
    path = safe_return_path(return_path)
    if user.accepted_tos is None:
        return f'/accept-tos?redirect_url={quote(path, safe="")}'
    if user.onboarding_completed is False:
        first_owner = await session.scalar(
            select(User.id)
            .join(OrgMember, OrgMember.user_id == User.id)
            .join(Role, Role.id == OrgMember.role_id)
            .where(
                OrgMember.org_id == user.current_org_id,
                Role.name == 'owner',
                User.accepted_tos.is_not(None),
            )
            .order_by(User.accepted_tos)
            .limit(1)
        )
        if DEPLOYMENT_MODE == 'cloud' or (
            DEPLOYMENT_MODE == 'self_hosted' and first_owner == user.id
        ):
            return f'/onboarding?returnTo={quote(path, safe="")}'
    return path


class NativeAuthService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.sessions: SessionFactory = session_factory or a_session_maker

    async def throttle(
        self, namespace: str, client_ip: str, email: str | None = None
    ) -> None:
        """Commit limits before credential verification, including failed attempts."""
        now = _now()
        cutoff = now - timedelta(seconds=300)
        keys = [(f'{namespace}:global', 1000), (f'{namespace}:ip:{client_ip}', 50)]
        if email is not None:
            keys.append((f'{namespace}:account:{email}', 10))
        blocked = False
        try:
            async with self.sessions() as session, session.begin():
                # Stale counters do not accumulate, even without a running cron job.
                await session.execute(
                    delete(AuthThrottle).where(AuthThrottle.window_start < cutoff)
                )
                for key, limit in keys:
                    stmt = insert(AuthThrottle).values(
                        key_digest=digest_token(key, 'throttle'),
                        window_start=now,
                        attempts=1,
                    )
                    counter_stmt = stmt.on_conflict_do_update(
                        index_elements=[AuthThrottle.key_digest],
                        set_={
                            'attempts': case(
                                (AuthThrottle.window_start < cutoff, 1),
                                else_=AuthThrottle.attempts + 1,
                            ),
                            'window_start': case(
                                (AuthThrottle.window_start < cutoff, now),
                                else_=AuthThrottle.window_start,
                            ),
                        },
                    ).returning(AuthThrottle.attempts)
                    attempts = await session.scalar(counter_stmt)
                    blocked = blocked or bool(attempts and attempts > limit)
        except SQLAlchemyError as exc:
            raise NativeAuthError(
                'Authentication temporarily unavailable', 503
            ) from exc
        if blocked:
            raise NativeAuthError(
                'Too many authentication attempts; try again later', 429
            )

    async def get_identity(self, account_id: UUID) -> NativePrincipal | None:
        from server.services.native_account_service import account_admitted

        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(AuthAccount, User)
                    .join(User, User.id == AuthAccount.id)
                    .where(AuthAccount.id == account_id)
                )
            ).first()
            if row is None or row[0].state != 'profile_present' or row[1].is_disabled:
                return None
            if row[0].display_email is None or not await account_admitted(
                session, row[0]
            ):
                return None
            return NativePrincipal(account_id=account_id, email=row[0].display_email)

    async def authenticate_session(self, token: str) -> NativePrincipal | None:
        async with self.sessions() as session:
            tokens = SQLAlchemyAccessTokenDatabase[BrowserSession](
                session, BrowserSession
            )
            manager = PasswordUserManager(session)
            strategy: DatabaseStrategy[PasswordUser, UUID, BrowserSession] = (
                DatabaseStrategy(tokens)
            )
            user = await strategy.read_token(token, manager)
            # Equivalent to FastAPI Users' current_user(active=True). Disabled
            # users are denied without deleting their database sessions.
            if user is None or not user.is_active:
                return None
            stored = await tokens.get_by_token(token)
            if stored is None:
                return None
            return NativePrincipal(user.id, user.email, stored.id)

    async def revoke_session(self, token: str) -> None:
        async with self.sessions() as session:
            tokens = SQLAlchemyAccessTokenDatabase[BrowserSession](
                session, BrowserSession
            )
            strategy: DatabaseStrategy[PasswordUser, UUID, BrowserSession] = (
                DatabaseStrategy(tokens)
            )
            manager = PasswordUserManager(session)
            user = await strategy.read_token(token, manager)
            if user is not None:
                await strategy.destroy_token(token, user)

    async def _new_session(
        self, account_id: UUID, return_path: str | None = None
    ) -> NativeLogin:
        async with self.sessions() as session:
            manager = PasswordUserManager(session)
            identity = await manager.get(account_id)
            user = await session.get(User, account_id)
            if not identity.is_active or user is None:
                raise NativeAuthError('Account is unavailable', 401)
            now = _now().replace(tzinfo=None)
            user.first_login_at = user.first_login_at or now
            user.last_login_at = now
            tokens = SQLAlchemyAccessTokenDatabase[BrowserSession](
                session, BrowserSession
            )
            strategy: DatabaseStrategy[PasswordUser, UUID, BrowserSession] = (
                DatabaseStrategy(tokens)
            )
            token = await strategy.write_token(identity)
            stored = await tokens.get_by_token(token)
            if stored is None:
                raise NativeAuthError('Authentication temporarily unavailable', 503)
            return NativeLogin(
                NativePrincipal(account_id, identity.email, stored.id),
                token,
                await _redirect(session, user, return_path),
            )

    async def login(
        self,
        email: str,
        password: str,
        *,
        client_ip: str,
        return_path: str | None = None,
    ) -> NativeLogin:
        try:
            normalized = normalize_email(email)
        except NativeAuthError:
            normalized = 'invalid-email'
        await self.throttle('login', client_ip, normalized)
        async with self.sessions() as session:
            manager = PasswordUserManager(session)
            if normalized == 'invalid-email' or len(password.encode('utf-8')) > 4096:
                raise NativeAuthError('Invalid email or password', 401)
            identity = await manager.authenticate(
                OAuth2PasswordRequestForm(username=normalized, password=password)
            )
        if identity is None:
            raise NativeAuthError('Invalid email or password', 401)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            credential = await session.scalar(
                select(PasswordCredential)
                .where(PasswordCredential.normalized_login_email == normalized)
                .with_for_update()
            )
            if credential is None:
                raise NativeAuthError('Invalid email or password', 401)
            from server.services.native_account_service import email_admitted

            account = await session.get(
                AuthAccount, credential.account_id, with_for_update=True
            )
            user = await session.get(User, credential.account_id)
            if account is None or account.state not in (
                'profile_present',
                'reonboardable',
            ):
                raise NativeAuthError('Invalid email or password', 401)
            if (
                account.normalized_email is None
                or account.display_email is None
                or not await email_admitted(session, account.normalized_email)
            ):
                raise NativeAuthError('Invalid email or password', 401)
            if account.state == 'reonboardable' and user is None:
                user = await create_profile(session, account, account.display_email)
            elif account.state != 'profile_present' or user is None or user.is_disabled:
                raise NativeAuthError('Invalid email or password', 401)
        return await self._new_session(identity.id, return_path)

    @staticmethod
    async def authentication_methods(
        session: AsyncSession, account_id: UUID
    ) -> list[str]:
        return (
            ['password']
            if await session.get(PasswordCredential, account_id) is not None
            else []
        )

    async def profile_metadata(self, account_id: UUID) -> NativeProfileMetadata:
        """Public local identity metadata, with no credentials or IdP subjects."""
        async with self.sessions() as session:
            account = await session.get(AuthAccount, account_id)
            if account is None or account.state != 'profile_present':
                raise NativeAuthError('Account is unavailable', 401)
            methods = await self.authentication_methods(session, account_id)
            return {
                'email': account.display_email,
                'has_password': 'password' in methods,
                'authentication_methods': methods,
            }

    async def cleanup_expired_state(self) -> None:
        """Remove expired throttle windows; library sessions have no default TTL."""
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(AuthThrottle).where(
                    AuthThrottle.window_start < _now() - timedelta(minutes=5)
                )
            )


@lru_cache(maxsize=1)
def get_native_auth_service() -> NativeAuthService:
    return NativeAuthService()
