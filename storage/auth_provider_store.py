"""Store for ``auth_providers`` — CRUD + IDP/git provider lookups.

The provider table is seeded by migration 186 (renamed+reshaped from the
earlier ``oauth_providers`` table) from environment variables / existing
rows, but runtime reads and config mutations go through this store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.utils.logger import openhands_logger as logger
from storage.auth_provider import AuthProvider
from storage.database import a_session_maker
from storage.oauth_token import OAuthToken


@dataclass
class AuthProviderStore:
    """CRUD + kind lookups for auth providers."""

    async def get_by_id(self, provider_id: int) -> AuthProvider | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider).where(AuthProvider.id == provider_id)
            )
            return result.scalars().one_or_none()

    async def _has_real_idp(self) -> bool:
        """Whether any real IDP provider exists in ``auth_providers``.

        Queries the DB directly without the dev IDP sentinel fallback. Used
        by ``_v2_get_idp_access_token`` to tell a dev-IDP-only session (no
        IDP token to refresh, by design) apart from a real-IDP session with
        a stale/missing token row (should raise ``ExpiredError``).
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider.id).where(AuthProvider.is_idp.is_(True)).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def get_idp_providers(
        self, *, enabled_only: bool = True
    ) -> list[AuthProvider]:
        """Return all IDP providers, including the integrated/password IDP.

        The integrated IDP (``server.routes.idp``) is a real row like any
        other (``kind='PasswordProviderDetails'``), so no special-casing is
        needed here.

        ``enabled_only`` filters out rows an admin has switched off. Pass
        ``False`` when resolving a session or token that was minted while the
        row was still enabled -- disabling a provider must not sign existing
        users out.
        """
        async with a_session_maker() as session:
            query = select(AuthProvider).where(AuthProvider.is_idp.is_(True))
            if enabled_only:
                query = query.where(AuthProvider.enabled.is_(True))
            result = await session.execute(query.order_by(AuthProvider.id))
            return list(result.scalars().all())

    async def get_first_idp(self, *, enabled_only: bool = True) -> AuthProvider | None:
        """Return the IDP that ``/oauth/idp-login`` should use.

        Priority is whichever ``is_idp`` row was created most recently
        (``created_at`` descending, ``id`` descending as a tiebreaker for
        same-timestamp inserts) -- there is no special-casing of the
        integrated/password IDP vs. a configured real IDP: whichever was
        configured last wins. Returns ``None`` when no IDP is configured (or
        only disabled ones are).
        """
        async with a_session_maker() as session:
            query = select(AuthProvider).where(AuthProvider.is_idp.is_(True))
            if enabled_only:
                query = query.where(AuthProvider.enabled.is_(True))
            result = await session.execute(
                query.order_by(
                    AuthProvider.created_at.desc(), AuthProvider.id.desc()
                ).limit(1)
            )
            return result.scalars().one_or_none()

    async def get_first_by_kind(self, kind: str) -> AuthProvider | None:
        """Return the first provider matching ``kind`` (lowest ``id``).

        ``kind`` is ``type(details).__name__`` (e.g.
        ``'PasswordProviderDetails'``) -- the sole, non-brand-specific use of
        this is ``server.routes.idp``'s lookup of the integrated/password IDP
        row. Returns ``None`` when no match exists.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider)
                .where(AuthProvider.kind == kind)
                .order_by(AuthProvider.id)
                .limit(1)
            )
            return result.scalars().one_or_none()

    async def get_git_providers(
        self, *, enabled_only: bool = True
    ) -> list[AuthProvider]:
        """Return git providers (``is_idp=False``).

        ``enabled_only`` mirrors :meth:`get_idp_providers`: pass ``False``
        when resolving tokens for a user who linked the provider while it was
        still enabled.
        """
        async with a_session_maker() as session:
            query = select(AuthProvider).where(AuthProvider.is_idp.is_(False))
            if enabled_only:
                query = query.where(AuthProvider.enabled.is_(True))
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_provider_by_type(self, provider_type: str) -> AuthProvider | None:
        """Return the row whose ``details.provider_type`` is ``provider_type``.

        If several match, the lowest ``id`` wins.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider)
                .where(
                    AuthProvider.details['provider_type'].as_string() == provider_type
                )
                .order_by(AuthProvider.id)
                .limit(1)
            )
            return result.scalars().one_or_none()

    async def get_linked_git_provider(
        self, user_id: UUID, provider_type: str
    ) -> AuthProvider | None:
        """The git provider of ``provider_type`` the user holds an ``oauth_tokens`` row for.

        If the user linked several, the highest ``id`` wins.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider)
                .join(OAuthToken, OAuthToken.oauth_provider_id == AuthProvider.id)
                .where(
                    OAuthToken.user_id == user_id,
                    AuthProvider.is_idp.is_(False),
                    AuthProvider.details['provider_type'].as_string() == provider_type,
                )
                .order_by(AuthProvider.id.desc())
                .limit(1)
            )
            return result.scalars().one_or_none()

    async def list_all(self) -> list[AuthProvider]:
        async with a_session_maker() as session:
            result = await session.execute(select(AuthProvider))
            return list(result.scalars().all())

    async def create(self, provider: AuthProvider) -> AuthProvider:
        """Insert a new provider row, returning the persisted row.

        Unlike :meth:`upsert`, this always inserts -- callers (e.g. the
        ``auth_providers`` admin API) construct a fresh ``AuthProvider`` with
        no ``id`` set.
        """
        async with a_session_maker() as session:
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
            return provider

    async def update(self, provider_id: int, **fields) -> AuthProvider | None:
        """Patch an existing provider row with only the given fields.

        Returns the updated row, or ``None`` if ``provider_id`` does not
        exist. Unlike :meth:`upsert` (full-row replace), only the columns
        passed in ``fields`` are modified -- callers (e.g. the
        ``auth_providers`` admin API) pass ``model_dump(exclude_unset=True)``
        so omitted fields are left untouched. Assigning ``details`` here
        re-syncs ``kind`` automatically (``AuthProvider._sync_kind``).
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider).where(AuthProvider.id == provider_id)
            )
            existing = result.scalars().one_or_none()
            if existing is None:
                return None
            for key, value in fields.items():
                setattr(existing, key, value)
            await session.commit()
            await session.refresh(existing)
            return existing

    async def upsert(self, provider: AuthProvider) -> AuthProvider:
        """Insert or update a provider row, returning the persisted row.

        ``provider.id`` selects update-if-present; ``0``/``None`` inserts.
        """
        async with a_session_maker() as session:
            existing: AuthProvider | None = None
            if provider.id:
                result = await session.execute(
                    select(AuthProvider).where(AuthProvider.id == provider.id)
                )
                existing = result.scalars().one_or_none()
            if existing is not None:
                for col in (
                    'title',
                    'is_idp',
                    'icon',
                    'color',
                    'enabled',
                    'allow_registration',
                    'allow_match_by_email',
                    'details',
                ):
                    setattr(existing, col, getattr(provider, col))
                provider = existing
            else:
                session.add(provider)
            await session.commit()
            await session.refresh(provider)
            return provider

    async def delete(self, provider_id: int) -> None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProvider).where(AuthProvider.id == provider_id)
            )
            existing = result.scalars().one_or_none()
            if existing is not None:
                await session.delete(existing)
                await session.commit()
            else:
                logger.debug('auth_provider_store.delete:no_row:%s', provider_id)

    @classmethod
    def for_session(cls, session: AsyncSession) -> _ScopedProviderStore:
        """Return a store bound to a caller-managed session (for tests)."""
        return _ScopedProviderStore(session)


class _ScopedProviderStore:
    """Provider operations scoped to a caller-managed ``AsyncSession``.

    Used in tests so reads/writes share the test's session instead of opening
    their own connections. Mirrors the public methods of ``AuthProviderStore``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, provider_id: int) -> AuthProvider | None:
        result = await self._session.execute(
            select(AuthProvider).where(AuthProvider.id == provider_id)
        )
        return result.scalars().one_or_none()

    async def get_idp_providers(self) -> list[AuthProvider]:
        result = await self._session.execute(
            select(AuthProvider)
            .where(AuthProvider.is_idp.is_(True))
            .order_by(AuthProvider.id)
        )
        return list(result.scalars().all())

    async def get_first_idp(self) -> AuthProvider | None:
        result = await self._session.execute(
            select(AuthProvider)
            .where(AuthProvider.is_idp.is_(True))
            .order_by(AuthProvider.created_at.desc(), AuthProvider.id.desc())
            .limit(1)
        )
        return result.scalars().one_or_none()

    async def get_first_by_kind(self, kind: str) -> AuthProvider | None:
        result = await self._session.execute(
            select(AuthProvider)
            .where(AuthProvider.kind == kind)
            .order_by(AuthProvider.id)
            .limit(1)
        )
        return result.scalars().one_or_none()

    async def get_git_providers(self) -> list[AuthProvider]:
        result = await self._session.execute(
            select(AuthProvider).where(AuthProvider.is_idp.is_(False))
        )
        return list(result.scalars().all())


async def iter_provider_rows(session: AsyncSession) -> AsyncIterator[AuthProvider]:
    """Yield all provider rows in a caller-managed session."""
    result = await session.execute(select(AuthProvider))
    for row in result.scalars():
        yield row
