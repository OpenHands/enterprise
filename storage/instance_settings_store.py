"""Store for the one-row ``instance_settings`` table."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from storage.database import a_session_maker
from storage.instance_settings import InstanceSettings


class InstanceSettingsStore:
    @staticmethod
    async def get_or_create_instance_id() -> UUID:
        """This install's instance ID, created the first time it is asked for.

        Every replica shares the database, so they all get the same ID, and it
        never changes once set. When replicas race to create it, the first
        write wins and the others read it back. The row is created if it does
        not exist yet; the first-user insert in ``UserStore`` fills in its own
        columns on an existing row, so creating it here first is safe.
        """
        async with a_session_maker() as session:
            instance_id = await session.scalar(
                select(InstanceSettings.instance_id).where(InstanceSettings.id == 1)
            )
            if instance_id is not None:
                return instance_id

            stmt = insert(InstanceSettings).values(id=1, instance_id=uuid4())
            # The WHERE makes this a no-op when another replica got there
            # first, so RETURNING gives no row and the winner's ID is read below.
            created = await session.scalar(
                stmt.on_conflict_do_update(
                    index_elements=[InstanceSettings.id],
                    set_={'instance_id': stmt.excluded.instance_id},
                    where=InstanceSettings.instance_id.is_(None),
                ).returning(InstanceSettings.instance_id)
            )
            if created is None:
                created = await session.scalar(
                    select(InstanceSettings.instance_id).where(InstanceSettings.id == 1)
                )
            await session.commit()
        if created is None:
            raise RuntimeError('instance_settings has no instance_id after upsert')
        return created
