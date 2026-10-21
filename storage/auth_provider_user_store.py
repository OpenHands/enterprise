"""Store for ``auth_provider_users`` — external-identity → internal-user lookup.

The identity-resolution table: given a provider's external subject id (the OIDC
``sub``), find our ``User``. Separate from ``oauth_tokens`` so it survives
token rotation and is the primary login lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.auth_provider_user import AuthProviderUser
from storage.database import a_session_maker


@dataclass
class AuthProviderUserStore:
    """Lookup/link by ``(oauth_provider_id, external_subject_id)``."""

    async def get(
        self, oauth_provider_id: int, external_subject_id: str
    ) -> AuthProviderUser | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProviderUser).where(
                    AuthProviderUser.oauth_provider_id == oauth_provider_id,
                    AuthProviderUser.external_subject_id == external_subject_id,
                )
            )
            return result.scalars().one_or_none()

    async def get_by_user(
        self, user_id: UUID, oauth_provider_id: int
    ) -> AuthProviderUser | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProviderUser).where(
                    AuthProviderUser.user_id == user_id,
                    AuthProviderUser.oauth_provider_id == oauth_provider_id,
                )
            )
            return result.scalars().one_or_none()

    async def list_for_user(self, user_id: UUID) -> list[AuthProviderUser]:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProviderUser).where(AuthProviderUser.user_id == user_id)
            )
            return list(result.scalars().all())

    async def link(
        self,
        oauth_provider_id: int,
        user_id: UUID,
        external_subject_id: str,
        external_email: str | None = None,
    ) -> AuthProviderUser:
        """Create or update the link for ``(provider, external_subject)``.

        Returns the persisted row. If a link already exists for this provider
        + external subject, it is updated (re-pointed) to ``user_id``.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProviderUser).where(
                    AuthProviderUser.oauth_provider_id == oauth_provider_id,
                    AuthProviderUser.external_subject_id == external_subject_id,
                )
            )
            row = result.scalars().one_or_none()
            if row is not None:
                row.user_id = user_id
                row.external_email = external_email
            else:
                row = AuthProviderUser(
                    oauth_provider_id=oauth_provider_id,
                    user_id=user_id,
                    external_subject_id=external_subject_id,
                    external_email=external_email,
                )
                session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def unlink(self, oauth_provider_id: int, external_subject_id: str) -> None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProviderUser).where(
                    AuthProviderUser.oauth_provider_id == oauth_provider_id,
                    AuthProviderUser.external_subject_id == external_subject_id,
                )
            )
            row = result.scalars().one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    @classmethod
    def for_session(cls, session: AsyncSession) -> _ScopedProviderUserStore:
        return _ScopedProviderUserStore(session)


class _ScopedProviderUserStore:
    """Provider-user operations scoped to a caller-managed ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, oauth_provider_id: int, external_subject_id: str
    ) -> AuthProviderUser | None:
        result = await self._session.execute(
            select(AuthProviderUser).where(
                AuthProviderUser.oauth_provider_id == oauth_provider_id,
                AuthProviderUser.external_subject_id == external_subject_id,
            )
        )
        return result.scalars().one_or_none()

    async def link(
        self,
        oauth_provider_id: int,
        user_id: UUID,
        external_subject_id: str,
        external_email: str | None = None,
    ) -> AuthProviderUser:
        result = await self._session.execute(
            select(AuthProviderUser).where(
                AuthProviderUser.oauth_provider_id == oauth_provider_id,
                AuthProviderUser.external_subject_id == external_subject_id,
            )
        )
        row = result.scalars().one_or_none()
        if row is not None:
            row.user_id = user_id
            row.external_email = external_email
        else:
            row = AuthProviderUser(
                oauth_provider_id=oauth_provider_id,
                user_id=user_id,
                external_subject_id=external_subject_id,
                external_email=external_email,
            )
            self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row
