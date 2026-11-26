"""Real-DB tests for private sandbox spec access."""

import uuid

import pytest
from sqlalchemy import select

from storage import sandbox_spec_access_store as store
from storage.org import Org
from storage.sandbox_spec_access import StoredSandboxSpecOrgGrant


@pytest.fixture
def orgs(create_org):
    return create_org(), create_org(), create_org()


async def _rule(async_session_maker, name):
    async with async_session_maker() as session:
        return next(
            (r for r in await store.list_rules(session) if r.sandbox_name == name),
            None,
        )


async def test_specs_without_a_rule_are_public(async_session_maker, orgs):
    async with async_session_maker() as session:
        accessible = await store.filter_accessible(session, ['a', 'b'], orgs[0].id)

    assert accessible == {'a', 'b'}


async def test_private_spec_is_limited_to_granted_orgs(async_session_maker, orgs):
    granted, other, _ = orgs
    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [granted.id])
        await session.commit()

    async with async_session_maker() as session:
        names = ['private', 'public']
        assert await store.filter_accessible(session, names, granted.id) == set(names)
        assert await store.filter_accessible(session, names, other.id) == {'public'}
        assert await store.filter_accessible(session, names, None) == {'public'}


async def test_private_spec_with_no_grants_is_usable_by_nobody(
    async_session_maker, orgs
):
    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [])
        await session.commit()

    async with async_session_maker() as session:
        assert await store.filter_accessible(session, ['private'], orgs[0].id) == set()


async def test_set_rule_replaces_grants(async_session_maker, orgs):
    first, second, _ = orgs
    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [first.id])
        await store.set_rule(session, 'private', True, [second.id])
        await session.commit()

    rule = await _rule(async_session_maker, 'private')
    assert rule is not None
    assert rule.org_ids == (second.id,)


async def test_making_a_spec_public_drops_its_rule_and_grants(
    async_session_maker, orgs
):
    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [orgs[0].id])
        await store.set_rule(session, 'private', False, [orgs[0].id])
        await session.commit()

    assert await _rule(async_session_maker, 'private') is None
    async with async_session_maker() as session:
        grants = (await session.execute(select(StoredSandboxSpecOrgGrant))).all()
    assert grants == []


async def test_losing_access_clears_that_orgs_default(
    async_session_maker, session_maker, orgs
):
    keeps, loses, unrelated = orgs
    with session_maker() as session:
        for org in (keeps, loses):
            session.get(Org, org.id).default_sandbox_spec_name = 'private'  # type: ignore[union-attr]
        session.get(Org, unrelated.id).default_sandbox_spec_name = 'other'  # type: ignore[union-attr]
        session.commit()

    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [keeps.id])
        await session.commit()

    async with async_session_maker() as session:
        assert await store.get_org_default_spec_name(session, keeps.id) == 'private'
        assert await store.get_org_default_spec_name(session, loses.id) is None
        assert await store.get_org_default_spec_name(session, unrelated.id) == 'other'


async def test_deleting_a_rule_removes_its_grants(async_session_maker, orgs):
    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [orgs[0].id, orgs[1].id])
        await session.commit()

    async with async_session_maker() as session:
        await store.delete_rule(session, 'private')
        await session.commit()

    assert await _rule(async_session_maker, 'private') is None
    async with async_session_maker() as session:
        assert (await session.execute(select(StoredSandboxSpecOrgGrant))).all() == []


async def test_get_org_names_ignores_unknown_orgs(async_session_maker, orgs):
    async with async_session_maker() as session:
        names = await store.get_org_names(session, [orgs[0].id, uuid.uuid4()])

    assert names == {orgs[0].id: orgs[0].name}


async def test_deleting_an_org_removes_its_grants(
    async_session_maker, session_maker, orgs
):
    async with async_session_maker() as session:
        await store.set_rule(session, 'private', True, [orgs[0].id])
        await session.commit()

    with session_maker() as session:
        session.delete(session.get(Org, orgs[0].id))
        session.commit()

    rule = await _rule(async_session_maker, 'private')
    assert rule is not None
    assert rule.org_ids == ()
