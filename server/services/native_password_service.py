"""One-time password recovery and authenticated credential replacement."""

from datetime import timedelta
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.auth_config import get_native_auth_settings
from server.auth.native_password import NativeAuthError, hash_password, verify_password
from server.auth.native_session import digest_token, new_token
from server.auth.native_types import PasswordResetLink, SessionFactory
from server.services.native_account_service import (
    lock_native_lifecycle,
    require_active_admin,
    revoke_account_security,
)
from server.services.native_auth_service import (
    NativeAuthService,
    NativeLogin,
    _now,
    _valid_token,
)
from storage.native_auth import AuthAccount, AuthChallenge, PasswordCredential


class NativePasswordService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.auth = NativeAuthService(session_factory)
        self.sessions = self.auth.sessions

    async def issue_password_reset(
        self, creator_id: UUID, account_id: UUID, session_token: str
    ) -> PasswordResetLink:
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            await self.auth._recent(session, session_token, creator_id)
            await require_active_admin(session, creator_id)
            credential = await session.get(PasswordCredential, account_id)
            account = await session.get(AuthAccount, account_id)
            if account is None or account.state == 'deleted':
                raise NativeAuthError('Account not found', 404)
            if credential is None:
                raise NativeAuthError('This account has no password to reset', 409)
            token = new_token()
            challenge = AuthChallenge(
                token_digest=digest_token(token, 'password_reset'),
                purpose='password_reset',
                account_id=account_id,
                credential_version=credential.credential_version,
                creator_account_id=creator_id,
                expires_at=_now()
                + timedelta(seconds=get_native_auth_settings().reset_seconds),
            )
            session.add(challenge)
            return {
                'reset_url': f'{get_native_auth_settings().web_url}/password-reset#token={token}',
                'expires_at': challenge.expires_at,
            }

    async def complete_password_reset(
        self, token: str, new_password: str, *, client_ip: str
    ) -> None:
        await self.auth.throttle('password_reset', client_ip)
        if not _valid_token(token):
            raise NativeAuthError('Invalid or expired password reset link')
        # Validate before Argon2 work; recheck everything under lock after hashing.
        async with self.sessions() as session:
            exists = await session.scalar(
                select(AuthChallenge.id).where(
                    AuthChallenge.token_digest == digest_token(token, 'password_reset'),
                    AuthChallenge.purpose == 'password_reset',
                    AuthChallenge.expires_at > _now(),
                    AuthChallenge.consumed_at.is_(None),
                    AuthChallenge.revoked_at.is_(None),
                )
            )
            if exists is None:
                raise NativeAuthError('Invalid or expired password reset link')
        password_hash = await hash_password(new_password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            challenge = await session.scalar(
                select(AuthChallenge)
                .where(
                    AuthChallenge.token_digest == digest_token(token, 'password_reset'),
                    AuthChallenge.purpose == 'password_reset',
                )
                .with_for_update()
            )
            if (
                challenge is None
                or challenge.expires_at <= _now()
                or challenge.consumed_at is not None
                or challenge.revoked_at is not None
            ):
                raise NativeAuthError('Invalid or expired password reset link')
            if challenge.creator_account_id is not None:
                await require_active_admin(session, challenge.creator_account_id)
            account = await session.get(AuthAccount, challenge.account_id)
            credential = await session.get(
                PasswordCredential, challenge.account_id, with_for_update=True
            )
            if (
                account is None
                or account.state == 'deleted'
                or credential is None
                or credential.credential_version != challenge.credential_version
            ):
                raise NativeAuthError('Invalid or expired password reset link')
            challenge.consumed_at = _now()
            await self._replace_password(session, credential, password_hash)

    async def _replace_password(
        self, session: AsyncSession, credential: PasswordCredential, password_hash: str
    ) -> None:
        credential.password_hash = password_hash
        credential.credential_version += 1
        credential.changed_at = _now()
        await revoke_account_security(session, credential.account_id)

    async def change_password(
        self,
        account_id: UUID,
        session_token: str,
        current_password: str,
        new_password: str,
        *,
        client_ip: str,
    ) -> NativeLogin:
        await self.auth.throttle('password_change', client_ip, str(account_id))
        async with self.sessions() as session, session.begin():
            row = await self.auth._recent(session, session_token, account_id)
            if row[2] is None:
                raise NativeAuthError('This account has no password to change', 409)
            known_hash = row[2].password_hash
        if not await verify_password(known_hash, current_password):
            raise NativeAuthError('Invalid current password', 401)
        password_hash = await hash_password(new_password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            _, account, credential, user = await self.auth._recent(
                session, session_token, account_id
            )
            if credential is None or credential.password_hash != known_hash:
                raise NativeAuthError('Sign in again to perform this action', 401)
            await self._replace_password(session, credential, password_hash)
            await session.flush()
            await session.refresh(account)
            return await self.auth._new_session(session, account, credential, user)

    async def recover_password(self, account_id: UUID, new_password: str) -> None:
        password_hash = await hash_password(new_password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            account = await session.get(AuthAccount, account_id, with_for_update=True)
            credential = await session.get(
                PasswordCredential, account_id, with_for_update=True
            )
            if account is None or account.state == 'deleted':
                raise NativeAuthError('Account not found', 404)
            if credential is None:
                raise NativeAuthError('This account has no password to recover', 409)
            await self._replace_password(session, credential, password_hash)


@lru_cache(maxsize=1)
def get_native_password_service() -> NativePasswordService:
    return NativePasswordService()
