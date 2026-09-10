"""Explicit, collision-safe external identity mappings."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from server.auth.contracts import InvalidCredentials
from storage.database import a_session_maker
from storage.external_identities import ExternalIdentity
from storage.user import User


class IdentityRepository:
    async def resolve(self, connection: str, issuer: str, subject: str) -> UUID | None:
        async with a_session_maker() as session:
            return await session.scalar(
                select(ExternalIdentity.user_id).where(
                    ExternalIdentity.connection == connection,
                    ExternalIdentity.issuer == issuer,
                    ExternalIdentity.subject == subject,
                )
            )

    async def link(
        self, user_id: UUID, connection: str, issuer: str, subject: str
    ) -> None:
        if not all((connection, issuer, subject)):
            raise InvalidCredentials(
                'An external identity requires connection, issuer and subject'
            )
        try:
            async with a_session_maker() as session, session.begin():
                user = await session.scalar(
                    select(User).where(User.id == user_id).with_for_update()
                )
                if user is None or user.is_disabled:
                    raise InvalidCredentials('Account is unavailable')
                existing = await session.scalar(
                    select(ExternalIdentity).where(
                        ExternalIdentity.connection == connection,
                        ExternalIdentity.issuer == issuer,
                        ExternalIdentity.subject == subject,
                    )
                )
                if existing is not None:
                    if existing.user_id != user_id:
                        raise InvalidCredentials(
                            'External identity belongs to another account'
                        )
                    return
                session.add(
                    ExternalIdentity(
                        user_id=user_id,
                        connection=connection,
                        issuer=issuer,
                        subject=subject,
                    )
                )
        except IntegrityError:
            # A simultaneous proof for the same identity may be idempotent, but
            # can never transfer it from one canonical account to another.
            if await self.resolve(connection, issuer, subject) != user_id:
                raise InvalidCredentials(
                    'External identity belongs to another account'
                ) from None
