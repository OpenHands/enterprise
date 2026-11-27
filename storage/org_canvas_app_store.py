"""Store for the Agent Canvas Apps approved for an organization (OHE-3773).

Every member can list the organization's apps and switch each one on or off
for themselves. Callers authorize who may add or remove an app.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from storage.database import a_session_maker
from storage.org_canvas_app import StoredOrgCanvasApp, StoredOrgMemberCanvasApp


class OrgCanvasAppAlreadyExistsError(Exception):
    """Raised when the organization already has an app of that name."""


class OrgCanvasAppNotFoundError(Exception):
    """Raised when the organization has no app of that name."""


@dataclass
class OrgCanvasAppStore:
    org_id: UUID

    async def list_for(self, user_id: UUID) -> list[tuple[StoredOrgCanvasApp, bool]]:
        """The organization's apps, each with whether ``user_id`` enabled it."""
        async with a_session_maker() as session:
            apps = (
                (
                    await session.execute(
                        select(StoredOrgCanvasApp)
                        .where(StoredOrgCanvasApp.org_id == self.org_id)
                        .order_by(StoredOrgCanvasApp.name)
                    )
                )
                .scalars()
                .all()
            )
            enabled = set(
                (
                    await session.execute(
                        select(StoredOrgMemberCanvasApp.name).where(
                            StoredOrgMemberCanvasApp.org_id == self.org_id,
                            StoredOrgMemberCanvasApp.user_id == user_id,
                        )
                    )
                ).scalars()
            )
            return [(app, app.name in enabled) for app in apps]

    async def create(self, app: StoredOrgCanvasApp) -> None:
        async with a_session_maker() as session:
            session.add(app)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise OrgCanvasAppAlreadyExistsError(app.name) from exc

    async def delete(self, name: str) -> bool:
        """Remove the app, and with it every member's enablement of it.

        Returns whether the organization had an app of that name.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                delete(StoredOrgCanvasApp).where(
                    StoredOrgCanvasApp.org_id == self.org_id,
                    StoredOrgCanvasApp.name == name,
                )
            )
            await session.commit()
        return result.rowcount > 0

    async def set_enabled(self, user_id: UUID, name: str, enabled: bool) -> None:
        """Switch the app on or off for ``user_id`` only."""
        async with a_session_maker() as session:
            if enabled:
                try:
                    await session.execute(
                        pg_insert(StoredOrgMemberCanvasApp)
                        .values(org_id=self.org_id, user_id=user_id, name=name)
                        .on_conflict_do_nothing()
                    )
                except IntegrityError as exc:
                    # The foreign key to the app: no such app in this org.
                    raise OrgCanvasAppNotFoundError(name) from exc
            else:
                await session.execute(
                    delete(StoredOrgMemberCanvasApp).where(
                        StoredOrgMemberCanvasApp.org_id == self.org_id,
                        StoredOrgMemberCanvasApp.user_id == user_id,
                        StoredOrgMemberCanvasApp.name == name,
                    )
                )
            await session.commit()

    async def get_bundle(self, name: str) -> bytes | None:
        async with a_session_maker() as session:
            return (
                await session.execute(
                    select(StoredOrgCanvasApp.bundle).where(
                        StoredOrgCanvasApp.org_id == self.org_id,
                        StoredOrgCanvasApp.name == name,
                    )
                )
            ).scalar_one_or_none()
