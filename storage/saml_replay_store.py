"""PostgreSQL uniqueness makes replay decisions consistent across replicas."""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from storage.database import a_session_maker
from storage.saml_consumed_message import SamlConsumedMessage


class SamlReplayStore:
    @staticmethod
    async def consume(
        provider_id: int, request_id: str, assertion_id: str, expires_at: datetime
    ) -> bool:
        """Commit before returning so another replica cannot accept either ID."""
        async with a_session_maker() as session:
            async with session.begin():
                for kind, message_id in (
                    ('request', request_id),
                    ('assertion', assertion_id),
                ):
                    result = await session.execute(
                        insert(SamlConsumedMessage)
                        .values(
                            provider_id=provider_id,
                            message_kind=kind,
                            message_id=message_id,
                            expires_at=expires_at,
                        )
                        .on_conflict_do_nothing(
                            index_elements=['provider_id', 'message_kind', 'message_id']
                        )
                        .returning(SamlConsumedMessage.id)
                    )
                    if result.scalar_one_or_none() is None:
                        await session.rollback()
                        return False
            return True

    @staticmethod
    async def delete_expired(now: datetime, batch_size: int = 100) -> int:
        """Skip locked rows so login cleanup cannot block another transaction."""
        async with a_session_maker() as session:
            async with session.begin():
                ids = (
                    select(SamlConsumedMessage.id)
                    .where(SamlConsumedMessage.expires_at < now)
                    .order_by(SamlConsumedMessage.expires_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                result = await session.execute(
                    delete(SamlConsumedMessage)
                    .where(SamlConsumedMessage.id.in_(ids))
                    .returning(SamlConsumedMessage.id)
                )
                count = len(result.scalars().all())
            return count
