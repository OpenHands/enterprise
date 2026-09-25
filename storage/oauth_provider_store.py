"""Store for ``oauth_providers`` — CRUD + IDP/git provider lookups.

The provider table is seeded by migration 168 from environment variables, but
runtime reads and (future) config mutations go through this store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.database import a_session_maker
from storage.oauth_provider import OAuthProvider


@dataclass
class OAuthProviderStore:
    """CRUD + category lookups for OAuth providers."""

    async def get_by_id(self, provider_id: int) -> OAuthProvider | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider).where(OAuthProvider.id == provider_id)
            )
            return result.scalars().one_or_none()

    async def get_by_category(self, category: ProviderType) -> list[OAuthProvider]:
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider).where(
                    OAuthProvider.provider_category == category.value
                )
            )
            return list(result.scalars().all())

    async def get_idp_providers(self) -> list[OAuthProvider]:
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider).where(OAuthProvider.is_idp.is_(True))
            )
            return list(result.scalars().all())

    async def get_git_providers(self) -> list[OAuthProvider]:
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider).where(OAuthProvider.is_idp.is_(False))
            )
            return list(result.scalars().all())

    async def list_all(self) -> list[OAuthProvider]:
        async with a_session_maker() as session:
            result = await session.execute(select(OAuthProvider))
            return list(result.scalars().all())

    async def upsert(self, provider: OAuthProvider) -> OAuthProvider:
        """Insert or update a provider row, returning the persisted row.

        ``provider.id`` selects update-if-present; ``0``/``None`` inserts.
        """
        async with a_session_maker() as session:
            existing: OAuthProvider | None = None
            if provider.id:
                result = await session.execute(
                    select(OAuthProvider).where(OAuthProvider.id == provider.id)
                )
                existing = result.scalars().one_or_none()
            if existing is not None:
                for col in OAuthProvider.__table__.columns:
                    if col.name == 'id':
                        continue
                    setattr(existing, col.name, getattr(provider, col.name))
                provider = existing
            else:
                session.add(provider)
            await session.commit()
            await session.refresh(provider)
            return provider

    async def delete(self, provider_id: int) -> None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider).where(OAuthProvider.id == provider_id)
            )
            existing = result.scalars().one_or_none()
            if existing is not None:
                await session.delete(existing)
                await session.commit()
            else:
                logger.debug('oauth_provider_store.delete:no_row:%s', provider_id)

    @classmethod
    def for_session(cls, session: AsyncSession) -> _ScopedProviderStore:
        """Return a store bound to a caller-managed session (for tests)."""
        return _ScopedProviderStore(session)


class _ScopedProviderStore:
    """Provider operations scoped to a caller-managed ``AsyncSession``.

    Used in tests so reads/writes share the test's session instead of opening
    their own connections. Mirrors the public methods of ``OAuthProviderStore``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, provider_id: int) -> OAuthProvider | None:
        result = await self._session.execute(
            select(OAuthProvider).where(OAuthProvider.id == provider_id)
        )
        return result.scalars().one_or_none()

    async def get_by_category(self, category: ProviderType) -> list[OAuthProvider]:
        result = await self._session.execute(
            select(OAuthProvider).where(
                OAuthProvider.provider_category == category.value
            )
        )
        return list(result.scalars().all())

    async def get_idp_providers(self) -> list[OAuthProvider]:
        result = await self._session.execute(
            select(OAuthProvider).where(OAuthProvider.is_idp.is_(True))
        )
        return list(result.scalars().all())

    async def get_git_providers(self) -> list[OAuthProvider]:
        result = await self._session.execute(
            select(OAuthProvider).where(OAuthProvider.is_idp.is_(False))
        )
        return list(result.scalars().all())


async def iter_provider_rows(session: AsyncSession) -> AsyncIterator[OAuthProvider]:
    """Yield all provider rows in a caller-managed session."""
    result = await session.execute(select(OAuthProvider))
    for row in result.scalars():
        yield row
