"""Password credential and session mutations with account-level serialization."""

from uuid import UUID

from fastapi.security import OAuth2PasswordRequestForm
from pydantic import SecretStr
from sqlalchemy import delete, select

from server.auth.contracts import InvalidCredentials, IssuedSession, UserProfile
from server.auth.local.accounts import validated_email
from server.auth.local.adapter import LocalUserManager
from server.auth.local.passwords import (
    PasswordPolicyError,
    hash_password,
    verify_password,
)
from server.auth.local.sessions import (
    issue_in_transaction,
    lock_account,
    revoke_all_in_transaction,
)
from server.auth.mode import SessionFactory
from storage.auth_action_tokens import AuthActionToken
from storage.database import a_session_maker
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.user import User


def profile(user: User) -> UserProfile:
    return UserProfile(
        user.id, user.email, bool(user.email_verified), user.is_disabled, user.role_id
    )


class LocalPasswordCredentialService:
    def __init__(self, session_factory: SessionFactory | None = None):
        self.session_factory = session_factory or a_session_maker

    async def _authenticate(
        self, session, email: str, password: SecretStr
    ) -> tuple[User, LocalCredentials]:
        user_id = await session.scalar(
            select(LocalCredentials.user_id).where(
                LocalCredentials.normalized_email == normalize_login_email(email)
            )
        )
        if user_id is None:
            await verify_password(password, None)
            raise InvalidCredentials('Invalid email or password.')
        # Lock disabled users too, so their password still gets equal-cost work.
        await session.scalar(select(User).where(User.id == user_id).with_for_update())
        await session.scalar(
            select(LocalCredentials)
            .where(LocalCredentials.user_id == user_id)
            .with_for_update()
        )
        manager = LocalUserManager(session)
        authenticated = await manager.authenticate(
            OAuth2PasswordRequestForm(
                username=email, password=password.get_secret_value(), scope=''
            )
        )
        if authenticated is None:
            raise InvalidCredentials('Invalid email or password.')
        return await lock_account(session, authenticated.id)

    async def verify(self, email: str, password: SecretStr) -> UserProfile:
        async with self.session_factory() as session, session.begin():
            user, _ = await self._authenticate(session, email, password)
            return profile(user)

    async def login(self, email: str, password: SecretStr) -> IssuedSession:
        async with self.session_factory() as session, session.begin():
            user, credential = await self._authenticate(session, email, password)
            return await issue_in_transaction(session, user, credential)

    async def establish(
        self,
        user_id: UUID,
        email: str,
        password: SecretStr,
        *,
        must_change_password: bool,
    ) -> None:
        email = validated_email(email)
        hashed = await hash_password(password)
        async with self.session_factory() as session, session.begin():
            user = await session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if (
                user is None
                or user.is_disabled
                or await session.get(LocalCredentials, user_id)
            ):
                raise InvalidCredentials('Credential cannot be established.')
            user.email = email
            user.email_verified = False
            session.add(
                LocalCredentials(
                    user_id=user_id,
                    normalized_email=email,
                    password_hash=hashed,
                    must_change_password=must_change_password,
                )
            )

    async def _change(
        self, session, user_id, current_password, new_password
    ) -> tuple[User, LocalCredentials]:
        user, credential = await lock_account(session, user_id)
        valid, _ = await verify_password(current_password, credential.password_hash)
        if not valid:
            raise InvalidCredentials('Invalid current password.')
        if current_password.get_secret_value() == new_password.get_secret_value():
            raise PasswordPolicyError('Choose a different password.')
        credential.password_hash = await hash_password(new_password)
        credential.must_change_password = False
        await revoke_all_in_transaction(session, user_id)
        await session.execute(
            delete(AuthActionToken).where(AuthActionToken.user_id == user_id)
        )
        return user, credential

    async def change(
        self, user_id: UUID, current_password: SecretStr, new_password: SecretStr
    ) -> None:
        async with self.session_factory() as session, session.begin():
            await self._change(session, user_id, current_password, new_password)

    async def change_and_issue(
        self, user_id: UUID, current_password: SecretStr, new_password: SecretStr
    ) -> IssuedSession:
        async with self.session_factory() as session, session.begin():
            user, credential = await self._change(
                session, user_id, current_password, new_password
            )
            return await issue_in_transaction(session, user, credential)

    async def reset(self, user_id: UUID, new_password: SecretStr) -> None:
        hashed = await hash_password(new_password)
        async with self.session_factory() as session, session.begin():
            _, credential = await lock_account(session, user_id)
            credential.password_hash = hashed
            credential.must_change_password = False
            await revoke_all_in_transaction(session, user_id)
            await session.execute(
                delete(AuthActionToken).where(AuthActionToken.user_id == user_id)
            )
