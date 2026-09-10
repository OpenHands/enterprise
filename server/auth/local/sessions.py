"""Opaque, revocable 24-hour browser sessions and FastAPI Users strategy."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi_users import BaseUserManager
from fastapi_users.authentication.strategy import Strategy
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.contracts import IssuedSession, Principal, SessionExpired
from server.auth.local.adapter import LocalUser
from server.auth.mode import SessionFactory
from storage.auth_sessions import AuthSession
from storage.database import a_session_maker
from storage.local_credentials import LocalCredentials
from storage.user import User

SESSION_COOKIE = 'oh_session'
SESSION_LIFETIME = timedelta(hours=24)


def token_digest(token: SecretStr) -> str:
    return hashlib.sha256(token.get_secret_value().encode()).hexdigest()


async def lock_account(
    session: AsyncSession, user_id: UUID
) -> tuple[User, LocalCredentials]:
    # All credential/session mutations lock User first, then its credentials.
    # Lifecycle disable can use the same User lock before revoking access.
    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    credential = await session.scalar(
        select(LocalCredentials)
        .where(LocalCredentials.user_id == user_id)
        .with_for_update()
    )
    if user is None or user.is_disabled or credential is None:
        raise SessionExpired('Authentication required.')
    return user, credential


async def issue_in_transaction(
    session: AsyncSession,
    user: User,
    credential: LocalCredentials,
    *,
    restricted: bool = False,
) -> IssuedSession:
    now = datetime.now(UTC)
    token = SecretStr(secrets.token_urlsafe(32))
    digest = token_digest(token)
    restricted = restricted or credential.must_change_password
    expires = now + SESSION_LIFETIME
    session.add(
        AuthSession(
            token_digest=digest,
            user_id=user.id,
            created_at=now,
            expires_at=expires,
            restricted=restricted,
        )
    )
    await session.flush()
    return IssuedSession(
        Principal(user.id, 'password', now, session_id=digest, restricted=restricted),
        token,
        expires,
    )


async def revoke_all_in_transaction(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


class DigestSessionStrategy(Strategy[LocalUser, UUID]):
    """FastAPI Users strategy and digest store used by the browser backend."""

    def __init__(self, session_factory: SessionFactory | None = None):
        self.session_factory = session_factory or a_session_maker

    async def issue(self, user_id: UUID, *, restricted: bool = False) -> IssuedSession:
        async with self.session_factory() as session, session.begin():
            user, credential = await lock_account(session, user_id)
            return await issue_in_transaction(
                session, user, credential, restricted=restricted
            )

    async def validate(self, token: SecretStr) -> Principal:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(AuthSession, User, LocalCredentials)
                    .join(User, User.id == AuthSession.user_id)
                    .join(LocalCredentials, LocalCredentials.user_id == User.id)
                    .where(AuthSession.token_digest == token_digest(token))
                )
            ).first()
            if row is None:
                raise SessionExpired('Authentication required.')
            record, user, credential = row
            if user.is_disabled or record.expires_at <= datetime.now(UTC):
                raise SessionExpired('Authentication required.')
            return Principal(
                user.id,
                'password',
                record.created_at,
                session_id=record.token_digest,
                restricted=record.restricted or credential.must_change_password,
            )

    async def revoke(self, token: SecretStr) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                delete(AuthSession).where(
                    AuthSession.token_digest == token_digest(token)
                )
            )

    async def revoke_all(self, user_id: UUID) -> None:
        async with self.session_factory() as session, session.begin():
            await revoke_all_in_transaction(session, user_id)

    async def read_token(
        self, token: str | None, user_manager: BaseUserManager[LocalUser, UUID]
    ) -> LocalUser | None:
        if not token:
            return None
        try:
            principal = await self.validate(SecretStr(token))
        except SessionExpired:
            return None
        return await user_manager.user_db.get(principal.user_id)

    async def write_token(self, user: LocalUser) -> str:
        issued = await self.issue(user.id)
        return issued.token.get_secret_value()

    async def destroy_token(self, token: str, user: LocalUser) -> None:
        await self.revoke(SecretStr(token))


class LocalBrowserSessionBackend:
    """OpenHands interface; no library-specific user types escape the adapter."""

    def __init__(self, session_factory: SessionFactory | None = None):
        self.strategy = DigestSessionStrategy(session_factory)

    async def issue(self, user_id: UUID, *, restricted: bool = False) -> IssuedSession:
        return await self.strategy.issue(user_id, restricted=restricted)

    async def validate(self, token: SecretStr) -> Principal:
        return await self.strategy.validate(token)

    async def revoke(self, token: SecretStr) -> None:
        await self.strategy.revoke(token)

    async def revoke_all(self, user_id: UUID) -> None:
        await self.strategy.revoke_all(user_id)
