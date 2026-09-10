"""FastAPI Users adapter over the authoritative OpenHands account tables."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users.db import BaseUserDatabase
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.local.passwords import (
    password_helper,
    validate_password,
    verify_password,
)
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.role import Role
from storage.user import User


@dataclass
class LocalUser:
    id: UUID
    email: str
    hashed_password: str = field(repr=False)
    is_active: bool
    is_superuser: bool
    is_verified: bool


class LocalUserDatabase(BaseUserDatabase[LocalUser, UUID]):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, id: UUID) -> LocalUser | None:
        row = (
            await self.session.execute(
                select(User, LocalCredentials, Role.name)
                .join(LocalCredentials, LocalCredentials.user_id == User.id)
                .outerjoin(Role, Role.id == User.role_id)
                .where(User.id == id)
            )
        ).first()
        if row is None:
            return None
        user, credential, role = row
        return LocalUser(
            id=user.id,
            email=credential.normalized_email,
            hashed_password=credential.password_hash,
            is_active=not user.is_disabled,
            is_superuser=role == 'admin',
            is_verified=bool(user.email_verified),
        )

    async def get_by_email(self, email: str) -> LocalUser | None:
        user_id = await self.session.scalar(
            select(LocalCredentials.user_id).where(
                LocalCredentials.normalized_email == normalize_login_email(email)
            )
        )
        return await self.get(user_id) if user_id else None

    async def update(self, user: LocalUser, update_dict: dict[str, Any]) -> LocalUser:
        # Only credential upgrades belong to the library. Account lifecycle,
        # email changes, roles and admission are OpenHands-owned operations.
        if set(update_dict) != {'hashed_password'}:
            raise ValueError('Account changes require the OpenHands lifecycle service.')
        credential = await self.session.get(LocalCredentials, user.id)
        if credential is None:
            raise ValueError('Account credential is missing.')
        credential.password_hash = update_dict['hashed_password']
        await self.session.flush()
        user.hashed_password = credential.password_hash
        return user

    async def create(self, create_dict: dict[str, Any]) -> LocalUser:
        raise ValueError('Account creation requires the OpenHands lifecycle service.')

    async def delete(self, user: LocalUser) -> None:
        raise ValueError('Account deletion requires the OpenHands lifecycle service.')


class LocalUserManager(UUIDIDMixin, BaseUserManager[LocalUser, UUID]):
    def __init__(self, session: AsyncSession):
        super().__init__(LocalUserDatabase(session), password_helper)

    async def validate_password(self, password: str, user: Any) -> None:
        validate_password(SecretStr(password))

    async def authenticate(
        self, credentials: OAuth2PasswordRequestForm
    ) -> LocalUser | None:
        # BaseUserManager's synchronous hash work would block FastAPI's loop.
        # Preserve its lookup/upgrade semantics with a dummy verification and
        # threaded pwdlib calls, including malformed hashes and inactive users.
        user = await self.user_db.get_by_email(credentials.username)
        valid, upgraded = await verify_password(
            SecretStr(credentials.password), user.hashed_password if user else None
        )
        if not valid or user is None or not user.is_active:
            return None
        if upgraded:
            user = await self.user_db.update(user, {'hashed_password': upgraded})
        return user
