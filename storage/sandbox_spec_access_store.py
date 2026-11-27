"""Reads and writes of private sandbox spec access. See storage.sandbox_spec_access."""

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.org import Org
from storage.sandbox_spec_access import (
    StoredSandboxSpecAccess,
    StoredSandboxSpecOrgGrant,
)


@dataclass(frozen=True)
class SandboxSpecAccessRule:
    sandbox_name: str
    is_private: bool
    org_ids: tuple[UUID, ...]


async def filter_accessible(
    session: AsyncSession, spec_names: Iterable[str], org_id: UUID | None
) -> set[str]:
    """Return the given spec names that ``org_id`` may use.

    A spec is accessible if it is public, or private and granted to the org.
    """
    names = set(spec_names)
    if not names:
        return set()
    private = set(
        (
            await session.execute(
                select(StoredSandboxSpecAccess.sandbox_name).where(
                    StoredSandboxSpecAccess.sandbox_name.in_(names),
                    StoredSandboxSpecAccess.is_private.is_(True),
                )
            )
        ).scalars()
    )
    if not private or org_id is None:
        return names - private
    granted = set(
        (
            await session.execute(
                select(StoredSandboxSpecOrgGrant.sandbox_name).where(
                    StoredSandboxSpecOrgGrant.sandbox_name.in_(private),
                    StoredSandboxSpecOrgGrant.org_id == org_id,
                )
            )
        ).scalars()
    )
    return (names - private) | granted


async def get_org_default_spec_name(session: AsyncSession, org_id: UUID) -> str | None:
    return (
        await session.execute(
            select(Org.default_sandbox_spec_name).where(Org.id == org_id)
        )
    ).scalar_one_or_none()


async def get_org_names(
    session: AsyncSession, org_ids: Iterable[UUID]
) -> dict[UUID, str]:
    ids = set(org_ids)
    if not ids:
        return {}
    rows = await session.execute(select(Org.id, Org.name).where(Org.id.in_(ids)))
    return {org_id: name for org_id, name in rows.all()}


async def list_rules(session: AsyncSession) -> list[SandboxSpecAccessRule]:
    grants: dict[str, list[UUID]] = {}
    for name, org_id in (
        await session.execute(
            select(
                StoredSandboxSpecOrgGrant.sandbox_name, StoredSandboxSpecOrgGrant.org_id
            ).order_by(StoredSandboxSpecOrgGrant.org_id)
        )
    ).all():
        grants.setdefault(name, []).append(org_id)
    access_rows = (
        (
            await session.execute(
                select(StoredSandboxSpecAccess).order_by(
                    StoredSandboxSpecAccess.sandbox_name
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        SandboxSpecAccessRule(
            sandbox_name=row.sandbox_name,
            is_private=row.is_private,
            org_ids=tuple(grants.get(row.sandbox_name, [])),
        )
        for row in access_rows
    ]


async def set_rule(
    session: AsyncSession, sandbox_name: str, is_private: bool, org_ids: Iterable[UUID]
) -> None:
    """Replace the access rule of a spec. A public spec keeps no grants.

    An org that loses access also loses the spec as its default.
    """
    org_ids = set(org_ids)
    if is_private:
        rows = await session.scalars(
            select(Org)
            .where(
                Org.default_sandbox_spec_name == sandbox_name, Org.id.not_in(org_ids)
            )
            .with_for_update()
        )
        for org in rows:
            org.default_sandbox_spec_name = None
    await session.execute(
        delete(StoredSandboxSpecOrgGrant).where(
            StoredSandboxSpecOrgGrant.sandbox_name == sandbox_name
        )
    )
    if not is_private:
        await session.execute(
            delete(StoredSandboxSpecAccess).where(
                StoredSandboxSpecAccess.sandbox_name == sandbox_name
            )
        )
        return
    existing = await session.get(StoredSandboxSpecAccess, sandbox_name)
    if existing is None:
        session.add(StoredSandboxSpecAccess(sandbox_name=sandbox_name, is_private=True))
        await session.flush()
    session.add_all(
        StoredSandboxSpecOrgGrant(sandbox_name=sandbox_name, org_id=org_id)
        for org_id in org_ids
    )


async def delete_rule(session: AsyncSession, sandbox_name: str) -> None:
    """Forget a spec, e.g. when its warm runtime config is deleted.

    Grants go with it through ON DELETE CASCADE.
    """
    await session.execute(
        delete(StoredSandboxSpecAccess).where(
            StoredSandboxSpecAccess.sandbox_name == sandbox_name
        )
    )
