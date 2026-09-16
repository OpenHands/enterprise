"""FastAPI Users integration for the existing account and credential tables."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from fastapi_users import BaseUserManager, UUIDIDMixin, exceptions, schemas
from fastapi_users.db import BaseUserDatabase
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.auth_config import get_native_auth_settings
from server.auth.native_password import normalize_email, validate_password
from storage.encrypt_utils import get_jwt_service
from storage.native_auth import AuthAccount, PasswordCredential
from storage.role import Role
from storage.user import User


@dataclass
class PasswordUser:
    id: UUID
    email: str
    hashed_password: str = field(repr=False)
    is_active: bool
    is_superuser: bool
    is_verified: bool = True


class PasswordHashUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    hashed_password: str


class PasswordUserDatabase(BaseUserDatabase[PasswordUser, UUID]):
    """Map library lookups and password writes to the existing application user.

    Account creation and deletion belong to invitation and application lifecycle
    services. No public registration or generic account mutation router is added.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id: UUID) -> PasswordUser | None:
        account = await self.session.get(AuthAccount, id)
        credential = await self.session.get(PasswordCredential, id)
        if account is None or credential is None or account.state == 'deleted':
            return None
        if account.display_email is None or account.normalized_email is None:
            return None
        from server.services.native_account_service import email_admitted

        profile = await self.session.get(User, id)
        role = (
            await self.session.get(Role, profile.role_id)
            if profile is not None and profile.role_id is not None
            else None
        )
        return PasswordUser(
            id=id,
            email=account.display_email,
            hashed_password=credential.password_hash,
            is_active=(
                account.state == 'profile_present'
                and profile is not None
                and not profile.is_disabled
                and await email_admitted(self.session, account.normalized_email)
            ),
            is_superuser=role is not None and role.name == 'admin',
        )

    async def get_by_email(self, email: str) -> PasswordUser | None:
        account_id = await self.session.scalar(
            select(PasswordCredential.account_id).where(
                PasswordCredential.normalized_login_email == normalize_email(email)
            )
        )
        return await self.get(account_id) if account_id is not None else None

    async def update(
        self, user: PasswordUser, update_dict: dict[str, object]
    ) -> PasswordUser:
        values = PasswordHashUpdate.model_validate(update_dict)
        credential = await self.session.get(PasswordCredential, user.id)
        if credential is None:
            raise exceptions.UserNotExists()
        credential.password_hash = values.hashed_password
        credential.changed_at = datetime.now(UTC)
        await self.session.commit()
        updated = await self.get(user.id)
        if updated is None:
            raise exceptions.UserNotExists()
        return updated


class PasswordUserManager(UUIDIDMixin, BaseUserManager[PasswordUser, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(PasswordUserDatabase(session))
        keys = get_jwt_service()
        self.reset_password_token_secret = keys.get_key(keys.default_key_id).key
        self.reset_password_token_lifetime_seconds = (
            get_native_auth_settings().reset_seconds
        )
        self.reset_token: str | None = None

    async def validate_password(
        self, password: str, user: PasswordUser | schemas.BaseUserCreate
    ) -> None:
        validate_password(password)

    async def on_after_forgot_password(
        self, user: PasswordUser, token: str, request: Request | None = None
    ) -> None:
        # Existing deployments share administrator-issued links without SMTP.
        # This manager is request-scoped; never cache it or expose it in logs.
        self.reset_token = token
