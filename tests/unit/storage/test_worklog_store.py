"""Worklog storage against PostgreSQL: concurrency, cascades, cleanup, paging."""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select

from storage.org import Org
from storage.user_data_cleanup import delete_user_data
from storage.worklog import Worklog, WorklogEntry
from storage.worklog_store import (
    EntryAuthor,
    WorklogConflictError,
    WorklogNotFoundError,
    WorklogStore,
)


@pytest.fixture(autouse=True)
def database(async_session_maker, monkeypatch):
    monkeypatch.setattr('storage.worklog_store.a_session_maker', async_session_maker)


@pytest.fixture
def store() -> WorklogStore:
    return WorklogStore()


@pytest.fixture
def org(create_org):
    return create_org()


async def _count(async_session_maker, model, *where) -> int:
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count()).select_from(model).where(*where)
        )


async def _write(store, org, factory, body='x', slug=None, author=None, run_id=None):
    return await store.add_entry(
        org.id,
        factory,
        slug,
        content_type='markdown',
        title=None,
        body=body,
        author=author or EntryAuthor('user', str(uuid.uuid4()), None),
        run_id=run_id,
    )


async def test_concurrent_ensure_default_creates_one_default(
    store, org, async_session_maker
):
    factory = uuid.uuid4()

    await asyncio.gather(*(store.ensure_default(org.id, factory) for _ in range(20)))

    assert (
        await _count(async_session_maker, Worklog, Worklog.factory_id == factory) == 1
    )
    assert await _count(async_session_maker, Worklog, Worklog.is_default) == 1


async def test_ensure_default_is_a_no_op_when_one_exists(store, org):
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)
    await store.update_worklog(org.id, factory, 'default', name='Main')

    await store.ensure_default(org.id, factory)

    [item] = await store.list_worklogs(org.id, factory)
    assert item.worklog.name == 'Main'


async def test_the_database_allows_at_most_one_default_per_factory(
    store, org, async_session_maker
):
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)
    other = await store.create_worklog(org.id, factory, 'other', 'Other')

    async with async_session_maker() as session:
        row = await session.get(Worklog, other.id)
        row.is_default = True
        with pytest.raises(Exception, match='uq_worklogs_one_default_per_factory'):
            await session.commit()


async def test_slug_and_name_are_unique_per_factory_not_per_org(store, org):
    one, two = uuid.uuid4(), uuid.uuid4()
    await store.create_worklog(org.id, one, 'notes', 'Notes')

    await store.create_worklog(org.id, two, 'notes', 'Notes')

    with pytest.raises(WorklogConflictError, match='slug'):
        await store.create_worklog(org.id, one, 'notes', 'Different')
    with pytest.raises(WorklogConflictError, match='name'):
        await store.create_worklog(org.id, one, 'different', 'Notes')


async def test_default_swaps_are_serialized_and_leave_one_default(
    store, org, async_session_maker
):
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)
    for slug in ('a', 'b', 'c', 'd'):
        await store.create_worklog(org.id, factory, slug, slug.upper())

    await asyncio.gather(
        *(
            store.update_worklog(org.id, factory, slug, make_default=True)
            for slug in ('a', 'b', 'c', 'd')
        )
    )

    assert (
        await _count(
            async_session_maker,
            Worklog,
            Worklog.factory_id == factory,
            Worklog.is_default,
        )
        == 1
    )


async def test_a_failed_rename_does_not_change_the_default(store, org):
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)
    await store.create_worklog(org.id, factory, 'a', 'A')

    with pytest.raises(WorklogConflictError):
        await store.update_worklog(
            org.id, factory, 'a', name='Default', make_default=True
        )

    items = {
        i.worklog.slug: i.worklog.is_default
        for i in await store.list_worklogs(org.id, factory)
    }
    assert items == {'default': True, 'a': False}


async def test_worklogs_are_scoped_by_org_and_factory(store, create_org, org):
    other_org = create_org()
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)

    assert await store.list_worklogs(other_org.id, factory) == []
    assert await store.list_worklogs(org.id, uuid.uuid4()) == []
    with pytest.raises(WorklogNotFoundError):
        await store.update_worklog(other_org.id, factory, 'default', name='x')
    with pytest.raises(WorklogNotFoundError):
        await _write(store, other_org, factory)


async def test_entries_are_returned_in_insertion_order_with_a_stable_id_tiebreak(
    store, org
):
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)
    for n in range(4):
        await _write(store, org, factory, body=str(n))

    rows = await store.list_entries(
        org.id, factory, None, after=None, limit=10, content_type=None
    )

    assert [r.body for r in rows] == ['0', '1', '2', '3']
    assert [r.id for r in rows] == sorted(r.id for r in rows)


async def test_purge_removes_a_factorys_worklogs_and_entries_and_is_idempotent(
    store, org, async_session_maker
):
    gone, kept = uuid.uuid4(), uuid.uuid4()
    for factory in (gone, kept):
        await store.ensure_default(org.id, factory)
        await store.create_worklog(org.id, factory, 'notes', 'Notes')
        await _write(store, org, factory)
        await _write(store, org, factory, slug='notes')

    first = await store.purge_factory(org.id, gone)
    second = await store.purge_factory(org.id, gone)

    assert (first, second) == (2, 0)
    assert await _count(async_session_maker, Worklog, Worklog.factory_id == gone) == 0
    assert await _count(async_session_maker, Worklog, Worklog.factory_id == kept) == 2
    assert await _count(async_session_maker, WorklogEntry) == 2


async def test_purge_is_scoped_to_the_org(store, create_org, org, async_session_maker):
    other_org = create_org()
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)

    removed = await store.purge_factory(other_org.id, factory)

    assert removed == 0
    assert await _count(async_session_maker, Worklog) == 1


async def test_deleting_an_org_removes_its_worklogs_and_entries(
    store, org, create_org, async_session_maker
):
    other_org = create_org()
    for owner in (org, other_org):
        factory = uuid.uuid4()
        await store.ensure_default(owner.id, factory)
        await _write(store, owner, factory)

    async with async_session_maker() as session:
        await session.execute(delete(Org).where(Org.id == org.id))
        await session.commit()

    assert await _count(async_session_maker, Worklog, Worklog.org_id == org.id) == 0
    assert await _count(async_session_maker, Worklog) == 1
    assert await _count(async_session_maker, WorklogEntry) == 1


async def test_deleting_a_user_anonymizes_their_entries_and_keeps_them(
    store, org, create_user, async_session_maker
):
    user = create_user(current_org_id=org.id)
    bystander = str(uuid.uuid4())
    factory = uuid.uuid4()
    await store.ensure_default(org.id, factory)
    await _write(
        store, org, factory, 'mine', author=EntryAuthor('user', str(user.id), None)
    )
    await _write(
        store, org, factory, 'theirs', author=EntryAuthor('user', bystander, None)
    )
    await _write(
        store,
        org,
        factory,
        'automation with the same id',
        author=EntryAuthor('automation', str(user.id), 'Orchestrator'),
        run_id=uuid.uuid4(),
    )

    async with async_session_maker() as session:
        await delete_user_data(session, user.id)
        await session.commit()

    rows = await store.list_entries(
        org.id, factory, None, after=None, limit=10, content_type=None
    )
    assert {r.body: (r.author_kind, r.author_id) for r in rows} == {
        'mine': ('user', None),
        'theirs': ('user', bystander),
        'automation with the same id': ('automation', str(user.id)),
    }


async def test_summaries_count_only_requested_factories_in_one_org(
    store, org, create_org
):
    other_org = create_org()
    one, two, untouched = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for factory in (one, two):
        await store.ensure_default(org.id, factory)
    await store.create_worklog(org.id, one, 'notes', 'Notes')
    await _write(store, org, one)
    await _write(store, org, one, slug='notes')
    await store.ensure_default(other_org.id, two)
    await _write(store, other_org, two)

    summaries = await store.summaries(org.id, [untouched, one, two])

    assert [(s.factory_id, s.worklog_count, s.entry_count) for s in summaries] == [
        (untouched, 0, 0),
        (one, 2, 2),
        (two, 1, 0),
    ]
    assert summaries[0].last_entry_at is None
    assert summaries[1].last_entry_at is not None
    assert summaries[2].last_entry_at is None


async def test_factories_with_worklogs_pages_through_every_pair_once(store, create_org):
    orgs = [create_org(), create_org()]
    expected = set()
    for owner in orgs:
        for _ in range(3):
            factory = uuid.uuid4()
            await store.ensure_default(owner.id, factory)
            await store.create_worklog(owner.id, factory, 'second', 'Second')
            expected.add((owner.id, factory))

    seen: list[tuple[uuid.UUID, uuid.UUID]] = []
    after = None
    while batch := await store.factories_with_worklogs(after, 4):
        seen += batch
        after = batch[-1]

    assert len(seen) == 6
    assert set(seen) == expected
    assert seen == sorted(seen)
