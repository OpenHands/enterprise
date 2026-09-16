"""Application account lookup selected once for the installation."""

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.auth.native_password import normalize_email
from server.auth.native_types import NativeProfileMetadata
from storage.database import a_session_maker
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore
from utils.identity import IDENTITY_CLAIMS, resolve_display_name


@dataclass(frozen=True)
class AccountInfo:
    user_id: str
    email: str | None
    email_verified: bool | None = None
    name: str | None = None
    username: str | None = None
    picture: str | None = None
    company: str | None = None


class AccountLookup(Protocol):
    async def get_profile_metadata(
        self, user_id: str
    ) -> NativeProfileMetadata | None: ...
    async def get_profile_for_update(
        self, session: AsyncSession, user_id: str
    ) -> User | None: ...
    async def get_user_by_id(self, user_id: str) -> User | None: ...
    async def get_user_by_email(self, email: str) -> User | None: ...
    async def get_account_info(self, user_id: str) -> AccountInfo | None: ...
    async def get_user_id_by_email(self, email: str) -> str | None: ...


class KeycloakAccountLookup:
    async def get_profile_metadata(self, user_id: str) -> NativeProfileMetadata | None:
        return None

    async def get_profile_for_update(
        self, session: AsyncSession, user_id: str
    ) -> User | None:
        user = await session.scalar(
            select(User)
            .options(selectinload(User.org_members))
            .where(User.id == UUID(user_id))
        )
        if user is not None:
            return user
        migrated = await self.get_user_by_id(user_id)
        return await session.merge(migrated) if migrated is not None else None

    async def get_user_by_id(self, user_id: str) -> User | None:
        user = await UserStore.get_user_by_id(user_id)
        if user is not None:
            return user
        while not await UserStore._acquire_user_creation_lock(user_id):
            await asyncio.sleep(2)
        try:
            user = await UserStore.get_user_by_id(user_id)
            if user is not None:
                return user
            async with a_session_maker() as session:
                legacy = await session.scalar(
                    select(UserSettings).where(
                        UserSettings.keycloak_user_id == user_id,
                        UserSettings.already_migrated.is_(False),
                    )
                )
            if legacy is None:
                return None
            from server.auth.token_manager import TokenManager
            from server.services.account_profile_provisioning import (
                KeycloakAccountProfileProvisioning,
            )

            identity = await TokenManager().get_user_info_from_user_id(user_id)
            if identity is None:
                return None
            user = await KeycloakAccountProfileProvisioning().migrate_user(
                user_id, legacy, IDENTITY_CLAIMS.validate_python(identity)
            )
            if user is not None:
                user.sync_analytics_consent_with_tos()
            return user
        finally:
            await UserStore._release_user_creation_lock(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        return await UserStore.get_user_by_email(email)

    async def get_account_info(self, user_id: str) -> AccountInfo | None:
        from server.auth.token_manager import TokenManager

        identity = await TokenManager().get_user_info_from_user_id(user_id)
        if identity is None:
            return None
        claims = IDENTITY_CLAIMS.validate_python(identity)
        return AccountInfo(
            user_id=user_id,
            email=identity.get('email'),
            email_verified=identity.get('emailVerified'),
            name=resolve_display_name(claims),
            username=identity.get('username'),
        )

    async def get_user_id_by_email(self, email: str) -> str | None:
        from server.auth.token_manager import TokenManager

        return await TokenManager().get_user_id_from_user_email(email)


class OpenHandsAccountLookup:
    async def get_profile_metadata(self, user_id: str) -> NativeProfileMetadata | None:
        from server.services.native_auth_service import get_native_auth_service

        return await get_native_auth_service().profile_metadata(UUID(user_id))

    async def get_profile_for_update(
        self, session: AsyncSession, user_id: str
    ) -> User | None:
        from storage.native_auth import AuthAccount

        user: User | None = await session.scalar(
            select(User)
            .options(selectinload(User.org_members))
            .join(AuthAccount, AuthAccount.id == User.id)
            .where(
                User.id == UUID(user_id),
                User.is_disabled.is_(False),
                AuthAccount.state == 'profile_present',
            )
        )
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        from storage.native_auth import AuthAccount

        try:
            account_id = UUID(user_id)
        except ValueError:
            return None
        async with a_session_maker() as session:
            user: User | None = await session.scalar(
                select(User)
                .options(selectinload(User.org_members))
                .join(AuthAccount, AuthAccount.id == User.id)
                .where(User.id == account_id, AuthAccount.state == 'profile_present')
            )
            if user is not None:
                user.sync_analytics_consent_with_tos()
            return user

    async def get_user_by_email(self, email: str) -> User | None:
        from storage.native_auth import AuthAccount

        if not email:
            return None
        async with a_session_maker() as session:
            user: User | None = await session.scalar(
                select(User)
                .options(selectinload(User.org_members))
                .join(AuthAccount, AuthAccount.id == User.id)
                .where(
                    AuthAccount.normalized_email == normalize_email(email),
                    AuthAccount.state == 'profile_present',
                )
            )
        return user

    async def get_account_info(self, user_id: str) -> AccountInfo | None:
        from server.services.native_auth_service import get_native_auth_service

        identity = await get_native_auth_service().get_identity(UUID(user_id))
        if identity is None:
            return None
        return AccountInfo(
            user_id=user_id, email=identity.email, username=identity.email
        )

    async def get_user_id_by_email(self, email: str) -> str | None:
        user = await self.get_user_by_email(email)
        if user is None or await self.get_account_info(str(user.id)) is None:
            return None
        return str(user.id)
