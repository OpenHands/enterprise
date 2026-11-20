"""Store for the one-row ``instance_settings`` table."""

from typing import NamedTuple
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from storage.database import a_session_maker
from storage.encrypt_utils import decrypt_value, encrypt_value
from storage.instance_settings import InstanceSettings


class SavedLicense(NamedTuple):
    """The license a Super Admin saved. At most one of the two is set."""

    license_key: str | None = None
    # An offline license file's contents.
    offline_license: str | None = None


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

    @staticmethod
    async def get_license() -> SavedLicense:
        """The license a Super Admin saved, or an empty one."""
        async with a_session_maker() as session:
            row = (
                await session.execute(
                    select(
                        InstanceSettings.license_key, InstanceSettings.offline_license
                    ).where(InstanceSettings.id == 1)
                )
            ).one_or_none()
        if row is None:
            return SavedLicense()
        return SavedLicense(
            license_key=decrypt_value(row.license_key) if row.license_key else None,
            offline_license=decrypt_value(row.offline_license)
            if row.offline_license
            else None,
        )

    @staticmethod
    async def save_license(
        *, license_key: str | None = None, offline_license: str | None = None
    ) -> None:
        """Save a license in place of the one saved before.

        Pass a license key or an offline license file's contents. The row is
        created if it does not exist yet.
        """
        values = {
            'license_key': encrypt_value(license_key) if license_key else None,
            'offline_license': encrypt_value(offline_license)
            if offline_license
            else None,
        }
        async with a_session_maker() as session:
            await session.execute(
                insert(InstanceSettings)
                .values(id=1, **values)
                .on_conflict_do_update(
                    index_elements=[InstanceSettings.id], set_=values
                )
            )
            await session.commit()
