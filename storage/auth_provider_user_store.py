"""Store for ``auth_provider_users`` — external-identity → internal-user lookup.

The identity-resolution table: given a provider's external subject id (the OIDC
``sub``), find our ``User``. Separate from ``oauth_tokens`` so it survives
token rotation and is the primary login lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.auth_provider import AuthProvider
from storage.auth_provider_user import AuthProviderUser
from storage.database import a_session_maker
from storage.user import User
from utils.sql import escape_ilike


def _apply_filter(query, filter_text: str | None):
    """OR-match ``filter_text`` against user email, external email and
    external subject id -- shared by ``list_by_provider`` and
    ``count_by_provider`` so their row sets always agree."""
    if not filter_text:
        return query
    pattern = f'%{escape_ilike(filter_text)}%'
    return query.where(
        or_(
            User.email.ilike(pattern, escape='\\'),
            AuthProviderUser.external_email.ilike(pattern, escape='\\'),
            AuthProviderUser.external_subject_id.ilike(pattern, escape='\\'),
        )
    )


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

    async def get_by_id(self, link_id: int) -> AuthProviderUser | None:
        """Look up a link row by its own primary key (not the usual
        ``(provider, external_subject)`` lookup). Used by the admin "User
        Links" endpoints, which address a specific row by id."""
        async with a_session_maker() as session:
            return await session.get(AuthProviderUser, link_id)

    async def list_by_provider(
        self,
        oauth_provider_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        filter_text: str | None = None,
    ) -> list[tuple[AuthProviderUser, User]]:
        """Paginated ``auth_provider_users`` rows for one provider, joined to
        ``User`` for the linked account's email.

        Backs the admin "User Links" table. ``filter_text`` matches, case
        insensitively, any of: the linked user's email, the external email,
        or the external subject id.
        """
        async with a_session_maker() as session:
            query = (
                select(AuthProviderUser, User)
                .join(User, User.id == AuthProviderUser.user_id)
                .where(AuthProviderUser.oauth_provider_id == oauth_provider_id)
            )
            query = _apply_filter(query, filter_text)
            query = query.order_by(AuthProviderUser.id).offset(offset).limit(limit)
            result = await session.execute(query)
            return [(link, user) for link, user in result.all()]

    async def count_by_provider(
        self, oauth_provider_id: int, *, filter_text: str | None = None
    ) -> int:
        """Total ``auth_provider_users`` rows for one provider, matching
        ``list_by_provider``'s filter."""
        async with a_session_maker() as session:
            query = (
                select(func.count(AuthProviderUser.id))
                .select_from(AuthProviderUser)
                .join(User, User.id == AuthProviderUser.user_id)
                .where(AuthProviderUser.oauth_provider_id == oauth_provider_id)
            )
            query = _apply_filter(query, filter_text)
            result = await session.execute(query)
            return result.scalar() or 0

    async def update_user(self, link_id: int, user_id: UUID) -> AuthProviderUser | None:
        """Re-point an existing link row to a different ``user_id``.

        Sets only ``user_id`` -- unlike ``link()``, it never touches
        ``external_email`` (that would require it to be passed in and
        defaulted, and callers of this method don't have a fresh value to
        offer; see ``link()``'s docstring for why that default is
        dangerous).
        """
        async with a_session_maker() as session:
            row = await session.get(AuthProviderUser, link_id)
            if row is None:
                return None
            row.user_id = user_id
            await session.commit()
            await session.refresh(row)
            return row

    async def get_git_user_ids(
        self, provider_type: str, external_subject_id: str
    ) -> set[UUID]:
        """Users who linked ``external_subject_id`` on any git provider of
        ``provider_type``. Two providers of one type (e.g. two GitLab hosts) can
        reuse an id, so the caller decides what more than one user means."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(AuthProviderUser.user_id)
                .join(
                    AuthProvider, AuthProvider.id == AuthProviderUser.oauth_provider_id
                )
                .where(
                    AuthProviderUser.external_subject_id == external_subject_id,
                    AuthProvider.is_idp.is_(False),
                    AuthProvider.details['provider_type'].as_string() == provider_type,
                )
            )
            return set(result.scalars().all())

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
