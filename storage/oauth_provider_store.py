"""Store for ``oauth_providers`` — CRUD + IDP/git provider lookups.

The provider table is seeded by migration 168 from environment variables
(real IDP/git providers) and migration 175 (the dev IDP's own row), but
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
from storage.oauth_provider import DEV_IDP_CATEGORY, OAuthProvider


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

    async def _has_real_idp(self) -> bool:
        """Whether any *real* (non-dev) IDP provider exists in ``oauth_providers``.

        Excludes the dev IDP's own seeded row (``DEV_IDP_CATEGORY``) so this
        stays safe to call from ``is_dev_idp_available()`` — otherwise the
        dev IDP row's mere existence would make it permanently unavailable.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider.id)
                .where(OAuthProvider.is_idp.is_(True))
                .where(OAuthProvider.provider_category != DEV_IDP_CATEGORY)
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def _dev_idp_active(self) -> bool:
        """Whether the dev IDP row should be surfaced as a usable IDP.

        Deferred import: ``server.routes.dev_idp`` imports ``OAuthProviderStore``
        at module level, so importing it back here at module level would be
        circular. This re-shares ``is_dev_idp_available()`` as the single
        source of truth for the gate (self-hosted + no other real IDP
        configured) instead of duplicating its logic.
        """
        from server.routes.dev_idp import is_dev_idp_available

        return await is_dev_idp_available()

    async def get_idp_providers(self) -> list[OAuthProvider]:
        """Return all IDP providers.

        The dev IDP's seeded row is excluded unless it is *active*
        (self-hosted, no other real IDP configured) — see ``_dev_idp_active``.
        Because ``_has_real_idp`` already requires the dev IDP to be the only
        ``is_idp`` row for it to be active, this never mixes the dev IDP with
        a real one.
        """
        async with a_session_maker() as session:
            query = select(OAuthProvider).where(OAuthProvider.is_idp.is_(True))
            if not await self._dev_idp_active():
                query = query.where(OAuthProvider.provider_category != DEV_IDP_CATEGORY)
            result = await session.execute(query.order_by(OAuthProvider.id))
            return list(result.scalars().all())

    async def get_first_idp(self) -> OAuthProvider | None:
        """Return the first IDP provider (lowest id), or ``None`` if none.

        The dev IDP's seeded row is excluded unless it is *active* — see
        ``get_idp_providers``.
        """
        async with a_session_maker() as session:
            query = select(OAuthProvider).where(OAuthProvider.is_idp.is_(True))
            if not await self._dev_idp_active():
                query = query.where(OAuthProvider.provider_category != DEV_IDP_CATEGORY)
            result = await session.execute(
                query.order_by(OAuthProvider.id).limit(1)
            )
            return result.scalars().one_or_none()

    async def get_first_by_category(self, category: str) -> OAuthProvider | None:
        """Return the first provider matching ``provider_category`` (lowest ``id``).

        ``category`` is the raw string value (e.g. ``'github'``) so callers do not
        need the ``ProviderType`` enum. Returns ``None`` when no match exists.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthProvider)
                .where(OAuthProvider.provider_category == category)
                .order_by(OAuthProvider.id)
                .limit(1)
            )
            return result.scalars().one_or_none()

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
            select(OAuthProvider)
            .where(OAuthProvider.is_idp.is_(True))
            .order_by(OAuthProvider.id)
        )
        return list(result.scalars().all())

    async def get_first_idp(self) -> OAuthProvider | None:
        result = await self._session.execute(
            select(OAuthProvider)
            .where(OAuthProvider.is_idp.is_(True))
            .order_by(OAuthProvider.id)
            .limit(1)
        )
        return result.scalars().one_or_none()

    async def get_first_by_category(self, category: str) -> OAuthProvider | None:
        result = await self._session.execute(
            select(OAuthProvider)
            .where(OAuthProvider.provider_category == category)
            .order_by(OAuthProvider.id)
            .limit(1)
        )
        return result.scalars().one_or_none()

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
