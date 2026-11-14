"""Database operations for worklogs and their entries.

Routes address a factory by id. ``factory_id`` has no foreign key (factories
live in the automation service's database), so everything here is scoped by
``(org_id, factory_id)`` and never trusts a worklog id from a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import a_session_maker
from storage.worklog import Worklog, WorklogEntry

DEFAULT_WORKLOG_SLUG = 'default'
DEFAULT_WORKLOG_NAME = 'Default'


class WorklogNotFoundError(Exception):
    """No worklog with that slug in the factory."""


class WorklogConflictError(Exception):
    """The slug or name is taken, or the operation is refused for the worklog."""


@dataclass(frozen=True)
class WorklogWithStats:
    worklog: Worklog
    entry_count: int
    json_entry_count: int
    last_entry_at: datetime | None


@dataclass(frozen=True)
class FactorySummary:
    factory_id: UUID
    worklog_count: int
    entry_count: int
    last_entry_at: datetime | None


@dataclass(frozen=True)
class EntryAuthor:
    kind: str
    id: str | None
    name: str | None


@dataclass(frozen=True)
class EntryCursor:
    """The ``(created_at, id)`` position of the last entry a page returned."""

    created_at: datetime
    id: int


def _scope(org_id: UUID, factory_id: UUID):
    return and_(Worklog.org_id == org_id, Worklog.factory_id == factory_id)


class WorklogStore:
    """Every method opens its own session and commits what it changes."""

    async def has_worklogs(self, org_id: UUID, factory_id: UUID) -> bool:
        async with a_session_maker() as session:
            return bool(
                await session.scalar(
                    select(Worklog.id).where(_scope(org_id, factory_id)).limit(1)
                )
            )

    async def ensure_default(self, org_id: UUID, factory_id: UUID) -> None:
        """Create the factory's ``default`` worklog if it has none.

        ``ON CONFLICT DO NOTHING`` makes concurrent first requests produce one.
        """
        async with a_session_maker() as session:
            await session.execute(
                pg_insert(Worklog)
                .values(
                    id=uuid4(),
                    org_id=org_id,
                    factory_id=factory_id,
                    slug=DEFAULT_WORKLOG_SLUG,
                    name=DEFAULT_WORKLOG_NAME,
                    is_default=True,
                )
                .on_conflict_do_nothing()
            )
            await session.commit()

    async def list_worklogs(
        self, org_id: UUID, factory_id: UUID
    ) -> list[WorklogWithStats]:
        async with a_session_maker() as session:
            rows = await session.execute(
                select(
                    Worklog,
                    func.count(WorklogEntry.id),
                    func.count(case((WorklogEntry.content_type == 'json', 1))),
                    func.max(WorklogEntry.created_at),
                )
                .outerjoin(WorklogEntry, WorklogEntry.worklog_id == Worklog.id)
                .where(_scope(org_id, factory_id))
                .group_by(Worklog.id)
                .order_by(Worklog.is_default.desc(), Worklog.created_at, Worklog.id)
            )
            return [
                WorklogWithStats(
                    worklog=worklog,
                    entry_count=entry_count,
                    json_entry_count=json_entry_count,
                    last_entry_at=last_entry_at,
                )
                for worklog, entry_count, json_entry_count, last_entry_at in rows
            ]

    async def get_worklog_with_stats(
        self, org_id: UUID, factory_id: UUID, slug: str
    ) -> WorklogWithStats:
        for item in await self.list_worklogs(org_id, factory_id):
            if item.worklog.slug == slug:
                return item
        raise WorklogNotFoundError(slug)

    async def create_worklog(
        self, org_id: UUID, factory_id: UUID, slug: str, name: str
    ) -> Worklog:
        async with a_session_maker() as session:
            worklog = Worklog(
                id=uuid4(),
                org_id=org_id,
                factory_id=factory_id,
                slug=slug,
                name=name,
                is_default=False,
            )
            session.add(worklog)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise WorklogConflictError(_conflict_message(exc)) from exc
            await session.refresh(worklog)
            return worklog

    async def update_worklog(
        self,
        org_id: UUID,
        factory_id: UUID,
        slug: str,
        *,
        name: str | None = None,
        make_default: bool = False,
    ) -> Worklog:
        """Rename a worklog and/or make it the default, in one transaction."""
        async with a_session_maker() as session:
            # Locking every worklog of the factory in id order serializes
            # concurrent default swaps without deadlocking.
            rows = (
                await session.scalars(
                    select(Worklog)
                    .where(_scope(org_id, factory_id))
                    .order_by(Worklog.id)
                    .with_for_update()
                )
            ).all()
            target = next((w for w in rows if w.slug == slug), None)
            if target is None:
                raise WorklogNotFoundError(slug)
            try:
                if name is not None and name != target.name:
                    target.name = name
                    await session.flush()
                if make_default and not target.is_default:
                    await session.execute(
                        update(Worklog)
                        .where(_scope(org_id, factory_id), Worklog.is_default)
                        .values(is_default=False, updated_at=func.now())
                    )
                    target.is_default = True
                    await session.flush()
                target.updated_at = func.now()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WorklogConflictError(_conflict_message(exc)) from exc
            await session.refresh(target)
            return target

    async def delete_worklog(self, org_id: UUID, factory_id: UUID, slug: str) -> None:
        async with a_session_maker() as session:
            worklog = await session.scalar(
                select(Worklog)
                .where(_scope(org_id, factory_id), Worklog.slug == slug)
                .with_for_update()
            )
            if worklog is None:
                raise WorklogNotFoundError(slug)
            if worklog.is_default:
                raise WorklogConflictError(
                    'The default worklog cannot be deleted. '
                    'Make another worklog the default first.'
                )
            await session.delete(worklog)
            await session.commit()

    async def add_entry(
        self,
        org_id: UUID,
        factory_id: UUID,
        worklog_slug: str | None,
        *,
        content_type: str,
        title: str | None,
        body: str,
        author: EntryAuthor,
        run_id: UUID | None,
    ) -> tuple[int, datetime]:
        """Append an entry. ``worklog_slug=None`` targets the default worklog."""
        async with a_session_maker() as session:
            worklog_id = await self._resolve_worklog_id(
                session, org_id, factory_id, worklog_slug
            )
            entry = WorklogEntry(
                worklog_id=worklog_id,
                content_type=content_type,
                title=title,
                author_kind=author.kind,
                author_id=author.id,
                author_name=author.name,
                run_id=run_id,
                body=body,
            )
            session.add(entry)
            await session.flush()
            await session.refresh(entry)
            entry_id, created_at = entry.id, entry.created_at
            await session.commit()
            return entry_id, created_at

    async def list_entries(
        self,
        org_id: UUID,
        factory_id: UUID,
        worklog_slug: str | None,
        *,
        after: EntryCursor | datetime | None,
        limit: int,
        content_type: str | None,
    ) -> list[WorklogEntry]:
        """Up to ``limit`` entries in ``(created_at, id)`` order after ``after``.

        A cursor continues exactly after the entry it names, so entries that
        share a timestamp are neither skipped nor repeated; a bare timestamp
        returns the entries created strictly after it.
        """
        async with a_session_maker() as session:
            worklog_id = await self._resolve_worklog_id(
                session, org_id, factory_id, worklog_slug
            )
            query = select(WorklogEntry).where(WorklogEntry.worklog_id == worklog_id)
            if isinstance(after, EntryCursor):
                query = query.where(
                    or_(
                        WorklogEntry.created_at > after.created_at,
                        and_(
                            WorklogEntry.created_at == after.created_at,
                            WorklogEntry.id > after.id,
                        ),
                    )
                )
            elif after is not None:
                query = query.where(WorklogEntry.created_at > after)
            if content_type is not None:
                query = query.where(WorklogEntry.content_type == content_type)
            rows = await session.scalars(
                query.order_by(WorklogEntry.created_at, WorklogEntry.id).limit(limit)
            )
            return list(rows)

    async def summaries(
        self, org_id: UUID, factory_ids: Sequence[UUID]
    ) -> list[FactorySummary]:
        """Totals per requested factory. Reads only; creates nothing."""
        async with a_session_maker() as session:
            per_worklog = (
                select(
                    Worklog.factory_id.label('factory_id'),
                    Worklog.id.label('worklog_id'),
                    func.count(WorklogEntry.id).label('entries'),
                    func.max(WorklogEntry.created_at).label('last_at'),
                )
                .outerjoin(WorklogEntry, WorklogEntry.worklog_id == Worklog.id)
                .where(Worklog.org_id == org_id, Worklog.factory_id.in_(factory_ids))
                .group_by(Worklog.factory_id, Worklog.id)
                .subquery()
            )
            rows = await session.execute(
                select(
                    per_worklog.c.factory_id,
                    func.count(per_worklog.c.worklog_id),
                    func.sum(per_worklog.c.entries),
                    func.max(per_worklog.c.last_at),
                ).group_by(per_worklog.c.factory_id)
            )
            found = {
                factory_id: FactorySummary(
                    factory_id=factory_id,
                    worklog_count=worklog_count,
                    entry_count=int(entry_count or 0),
                    last_entry_at=last_entry_at,
                )
                for factory_id, worklog_count, entry_count, last_entry_at in rows
            }
        return [
            found.get(factory_id, FactorySummary(factory_id, 0, 0, None))
            for factory_id in factory_ids
        ]

    async def purge_factory(self, org_id: UUID, factory_id: UUID) -> int:
        """Remove a factory's worklogs and entries. Idempotent."""
        async with a_session_maker() as session:
            result = await session.execute(
                delete(Worklog).where(_scope(org_id, factory_id))
            )
            await session.commit()
            return result.rowcount or 0

    async def factories_with_worklogs(
        self, after: tuple[UUID, UUID] | None, limit: int
    ) -> list[tuple[UUID, UUID]]:
        """``(org_id, factory_id)`` pairs holding worklogs, in key order.

        Pass the last pair of the previous batch as ``after`` to continue.
        """
        async with a_session_maker() as session:
            query = select(Worklog.org_id, Worklog.factory_id).distinct()
            if after is not None:
                query = query.where(
                    or_(
                        Worklog.org_id > after[0],
                        and_(Worklog.org_id == after[0], Worklog.factory_id > after[1]),
                    )
                )
            rows = await session.execute(
                query.order_by(Worklog.org_id, Worklog.factory_id).limit(limit)
            )
            return [(row.org_id, row.factory_id) for row in rows]

    @staticmethod
    async def _resolve_worklog_id(
        session: AsyncSession,
        org_id: UUID,
        factory_id: UUID,
        worklog_slug: str | None,
    ) -> UUID:
        """The named worklog, or the default one when no name is given. A name
        that matches nothing never falls back to the default."""
        condition = (
            Worklog.slug == worklog_slug
            if worklog_slug is not None
            else Worklog.is_default
        )
        worklog_id = await session.scalar(
            select(Worklog.id).where(_scope(org_id, factory_id), condition)
        )
        if worklog_id is None:
            raise WorklogNotFoundError(worklog_slug or DEFAULT_WORKLOG_SLUG)
        return worklog_id


def _conflict_message(exc: IntegrityError) -> str:
    detail = str(exc.orig)
    if 'uq_worklogs_factory_slug' in detail:
        return 'A worklog with this slug already exists in the factory.'
    if 'uq_worklogs_factory_name' in detail:
        return 'A worklog with this name already exists in the factory.'
    return 'The worklog conflicts with an existing one.'
