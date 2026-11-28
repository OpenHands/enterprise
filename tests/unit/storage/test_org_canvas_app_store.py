"""Real-DB tests for OrgCanvasAppStore (Agent Canvas Apps approved for an org)."""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from storage.org_canvas_app import StoredOrgCanvasApp
from storage.org_canvas_app_store import (
    OrgCanvasAppAlreadyExistsError,
    OrgCanvasAppNotFoundError,
    OrgCanvasAppStore,
)
from storage.org_member import OrgMember
from storage.role import Role

ORG_ID = UUID('d4444444-4444-4444-4444-444444444444')
ALICE = UUID('d5555555-5555-5555-5555-555555555555')
BOB = UUID('d6666666-6666-6666-6666-666666666666')
BUNDLE = b'export function activate() {}\n'


def _app(name: str = 'board', org_id: UUID = ORG_ID) -> StoredOrgCanvasApp:
    return StoredOrgCanvasApp(
        org_id=org_id,
        name=name,
        source='github:acme/canvas-apps',
        requested_ref='main',
        resolved_ref='a' * 40,
        repo_path=f'apps/{name}',
        manifest={
            'schema_version': 1,
            'name': name,
            'display_name': name.title(),
            'version': '0.1.0',
            'entrypoint': 'extension.js',
        },
        bundle=BUNDLE,
        installed_at=datetime(2026, 11, 27, tzinfo=UTC),
    )


@pytest.fixture
def store(async_session_maker, create_org):
    create_org(id=ORG_ID)
    with patch('storage.org_canvas_app_store.a_session_maker', async_session_maker):
        yield OrgCanvasAppStore(ORG_ID)


@pytest.fixture
def members(store, session_maker, create_user):
    """``org_member`` rows for Alice and Bob in ``ORG_ID``."""
    with session_maker() as session:
        role = Role(name=f'member-{uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.commit()
        role_id = role.id
    for user_id in (ALICE, BOB):
        create_user(id=user_id, current_org_id=ORG_ID)
        with session_maker() as session:
            session.add(
                OrgMember(
                    org_id=ORG_ID,
                    user_id=user_id,
                    role_id=role_id,
                    llm_api_key='k',
                    status='active',
                )
            )
            session.commit()


async def _enabled(store: OrgCanvasAppStore, user_id: UUID) -> dict[str, bool]:
    return {app.name: enabled for app, enabled in await store.list_for(user_id)}


async def test_a_member_switches_an_app_on_for_themselves_only(store, members):
    # Arrange
    await store.create(_app('board'))

    # Act
    await store.set_enabled(ALICE, 'board', True)

    # Assert
    assert await _enabled(store, ALICE) == {'board': True}
    assert await _enabled(store, BOB) == {'board': False}


async def test_a_member_switches_an_app_off_again(store, members):
    # Arrange
    await store.create(_app('board'))
    await store.set_enabled(ALICE, 'board', True)

    # Act
    await store.set_enabled(ALICE, 'board', False)

    # Assert
    assert await _enabled(store, ALICE) == {'board': False}


async def test_an_app_the_org_does_not_have_cannot_be_switched_on(store, members):
    with pytest.raises(OrgCanvasAppNotFoundError):
        await store.set_enabled(ALICE, 'missing', True)


async def test_an_org_cannot_have_two_apps_of_the_same_name(store):
    # Arrange
    await store.create(_app('board'))

    # Act / Assert
    with pytest.raises(OrgCanvasAppAlreadyExistsError):
        await store.create(_app('board'))


async def test_removing_an_app_switches_it_off_for_every_member(store, members):
    # Arrange
    await store.create(_app('board'))
    await store.set_enabled(ALICE, 'board', True)
    await store.set_enabled(BOB, 'board', True)

    # Act
    removed = await store.delete('board')

    # Assert
    assert removed is True
    assert await _enabled(store, ALICE) == {}
    # Approving the same name again does not bring anyone's choice back.
    await store.create(_app('board'))
    assert await _enabled(store, ALICE) == {'board': False}
    assert await _enabled(store, BOB) == {'board': False}


async def test_removing_an_app_the_org_does_not_have_reports_nothing_removed(store):
    assert await store.delete('missing') is False


async def test_the_stored_entrypoint_is_served_until_the_app_is_removed(store):
    # Arrange
    await store.create(_app('board'))

    # Act
    stored = await store.get_bundle('board')
    await store.delete('board')

    # Assert
    assert stored == BUNDLE
    assert await store.get_bundle('board') is None


async def test_apps_belong_to_one_org(store, members, async_session_maker, create_org):
    # Arrange
    other_org = create_org()
    await store.create(_app('board'))

    # Act
    with patch('storage.org_canvas_app_store.a_session_maker', async_session_maker):
        other = OrgCanvasAppStore(other_org.id)
        listed = await other.list_for(ALICE)
        bundle = await other.get_bundle('board')

    # Assert
    assert listed == []
    assert bundle is None


async def test_a_member_with_an_app_switched_on_can_still_leave_the_org(
    store, members, session_maker
):
    # Arrange
    await store.create(_app('board'))
    await store.set_enabled(ALICE, 'board', True)
    await store.set_enabled(BOB, 'board', True)

    # Act
    with session_maker() as session:
        session.execute(
            delete(OrgMember).where(
                OrgMember.org_id == ORG_ID, OrgMember.user_id == ALICE
            )
        )
        session.commit()

    # Assert
    assert await _enabled(store, BOB) == {'board': True}
