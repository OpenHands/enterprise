"""The Super Admin directory lists run against a real database, one page at a time."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.instance_admin import instance_admin_router
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

CALLER_USER_ID = str(uuid.uuid4())


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(instance_admin_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture(autouse=True)
def as_super_admin(async_session_maker):
    superadmin = MagicMock()
    superadmin.name = 'admin'
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=superadmin),
        ),
        patch('server.routes.instance_admin.a_session_maker', async_session_maker),
        patch('storage.user_store.a_session_maker', async_session_maker),
        patch('storage.role_store.a_session_maker', async_session_maker),
        patch('storage.org_member_store.a_session_maker', async_session_maker),
    ):
        yield


@pytest.fixture
async def roles(async_session_maker) -> dict[str, Role]:
    async with async_session_maker() as session:
        rows = {
            name: Role(name=name, rank=rank)
            for name, rank in (('owner', 1), ('admin', 2), ('member', 3))
        }
        session.add_all(rows.values())
        await session.commit()
        for row in rows.values():
            await session.refresh(row)
    return rows


async def _get(app: FastAPI, url: str, **params):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        return await client.get(url, params=params)


async def _add_org(async_session_maker, name: str, **kwargs) -> Org:
    org = Org(id=kwargs.pop('id', uuid.uuid4()), name=name, **kwargs)
    async with async_session_maker() as session:
        session.add(org)
        await session.commit()
    return org


async def _add_user(
    async_session_maker,
    email: str | None,
    home_org: Org,
    *,
    role_id: int | None = None,
    **kwargs,
) -> User:
    user = User(
        id=kwargs.pop('id', uuid.uuid4()),
        email=email,
        current_org_id=home_org.id,
        role_id=role_id,
        **kwargs,
    )
    async with async_session_maker() as session:
        session.add(user)
        await session.commit()
    return user


async def _add_member(
    async_session_maker, org: Org, user: User, role: Role, status: str = 'active'
) -> None:
    async with async_session_maker() as session:
        session.add(
            OrgMember(
                org_id=org.id,
                user_id=user.id,
                role_id=role.id,
                llm_api_key='key',
                status=status,
            )
        )
        await session.commit()


async def _invite(
    async_session_maker,
    org: Org,
    email: str,
    role: Role,
    inviter: User,
    *,
    status: str = OrgInvitation.STATUS_PENDING,
    expires_in: timedelta = timedelta(days=1),
) -> None:
    async with async_session_maker() as session:
        session.add(
            OrgInvitation(
                token=uuid.uuid4().hex,
                org_id=org.id,
                email=email,
                role_id=role.id,
                inviter_id=inviter.id,
                status=status,
                expires_at=datetime.now(UTC).replace(tzinfo=None) + expires_in,
            )
        )
        await session.commit()


# ── GET /api/admin/organizations ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_organizations_need_the_super_admin_permission(app):
    with patch(
        'server.auth.authorization.get_user_super_role',
        AsyncMock(return_value=None),
    ):
        resp = await _get(app, '/api/admin/organizations')

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_organizations_come_back_one_page_at_a_time(app, async_session_maker):
    for name in ('delta', 'alpha', 'charlie', 'bravo', 'echo'):
        await _add_org(async_session_maker, name)

    first = (await _get(app, '/api/admin/organizations', limit=2)).json()
    second = (await _get(app, '/api/admin/organizations', limit=2, page_id='2')).json()
    last = (await _get(app, '/api/admin/organizations', limit=2, page_id='4')).json()

    assert [o['name'] for o in first['items']] == ['alpha', 'bravo']
    assert [o['name'] for o in second['items']] == ['charlie', 'delta']
    assert [o['name'] for o in last['items']] == ['echo']
    assert (first['total'], first['current_page'], first['per_page']) == (5, 1, 2)
    assert (second['current_page'], last['current_page']) == (2, 3)


@pytest.mark.asyncio
async def test_organizations_count_members_of_the_listed_orgs_only(
    app, async_session_maker, roles
):
    acme = await _add_org(async_session_maker, 'acme')
    other = await _add_org(async_session_maker, 'other')
    empty = await _add_org(async_session_maker, 'empty')
    for i in range(3):
        user = await _add_user(async_session_maker, f'u{i}@acme.example', acme)
        await _add_member(async_session_maker, acme, user, roles['member'])
    lone = await _add_user(async_session_maker, 'lone@other.example', other)
    await _add_member(async_session_maker, other, lone, roles['owner'])

    items = (await _get(app, '/api/admin/organizations', q='acme')).json()['items']
    everything = (await _get(app, '/api/admin/organizations')).json()['items']

    assert [(o['name'], o['member_count']) for o in items] == [('acme', 3)]
    assert {o['name']: o['member_count'] for o in everything} == {
        'acme': 3,
        'other': 1,
        'empty': 0,
    }
    assert empty.id is not None


@pytest.mark.asyncio
async def test_a_personal_workspace_shares_its_id_with_its_owner(
    app, async_session_maker
):
    team = await _add_org(async_session_maker, 'team')
    personal = await _add_org(async_session_maker, 'user_workspace')
    await _add_user(async_session_maker, 'solo@example.com', personal, id=personal.id)

    body = (await _get(app, '/api/admin/organizations')).json()
    without_personal = (
        await _get(app, '/api/admin/organizations', include_personal='false')
    ).json()

    assert {o['name']: o['is_personal'] for o in body['items']} == {
        'team': False,
        'user_workspace': True,
    }
    assert [o['id'] for o in without_personal['items']] == [str(team.id)]
    assert without_personal['total'] == 1


@pytest.mark.asyncio
async def test_organizations_are_searched_by_name_or_contact_email(
    app, async_session_maker
):
    await _add_org(async_session_maker, 'Acme Corp')
    await _add_org(async_session_maker, 'Globex', contact_email='boss@acme.example')
    await _add_org(async_session_maker, 'Initech')

    body = (await _get(app, '/api/admin/organizations', q='ACME')).json()

    assert [o['name'] for o in body['items']] == ['Acme Corp', 'Globex']
    assert body['total'] == 2


@pytest.mark.asyncio
async def test_organization_search_treats_wildcards_literally(app, async_session_maker):
    await _add_org(async_session_maker, '100% pure')
    await _add_org(async_session_maker, 'Initech')

    body = (await _get(app, '/api/admin/organizations', q='%')).json()

    assert [o['name'] for o in body['items']] == ['100% pure']


@pytest.mark.asyncio
@pytest.mark.parametrize('page_id', ['abc', '-1', '1.5'])
async def test_a_malformed_page_id_is_rejected(app, page_id):
    resp = await _get(app, '/api/admin/organizations', page_id=page_id)

    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [0, 101])
async def test_a_page_cannot_be_unbounded(app, limit):
    resp = await _get(app, '/api/admin/directory/users', limit=limit)

    assert resp.status_code == 422


# ── GET /api/admin/directory/users ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_users_come_back_one_page_at_a_time(app, async_session_maker):
    home = await _add_org(async_session_maker, 'home')
    for email in ('d@x.example', 'a@x.example', 'c@x.example', 'b@x.example'):
        await _add_user(async_session_maker, email, home)

    first = (await _get(app, '/api/admin/directory/users', limit=3)).json()
    second = (
        await _get(app, '/api/admin/directory/users', limit=3, page_id='3')
    ).json()

    assert [u['email'] for u in first['items']] == [
        'a@x.example',
        'b@x.example',
        'c@x.example',
    ]
    assert [u['email'] for u in second['items']] == ['d@x.example']
    assert (first['total'], first['current_page'], first['per_page']) == (4, 1, 3)
    assert second['current_page'] == 2


@pytest.mark.asyncio
async def test_a_page_lists_the_memberships_of_its_users_only(
    app, async_session_maker, roles
):
    acme = await _add_org(async_session_maker, 'Acme')
    globex = await _add_org(async_session_maker, 'Globex')
    ada = await _add_user(async_session_maker, 'ada@x.example', acme)
    bob = await _add_user(async_session_maker, 'bob@x.example', acme)
    await _add_member(async_session_maker, globex, ada, roles['member'], 'suspended')
    await _add_member(async_session_maker, acme, ada, roles['owner'])
    await _add_member(async_session_maker, acme, bob, roles['admin'])

    items = (await _get(app, '/api/admin/directory/users', limit=1)).json()['items']

    assert [u['email'] for u in items] == ['ada@x.example']
    assert items[0]['memberships'] == [
        {
            'org_id': str(acme.id),
            'org_name': 'Acme',
            'role': 'owner',
            'status': 'active',
        },
        {
            'org_id': str(globex.id),
            'org_name': 'Globex',
            'role': 'member',
            'status': 'suspended',
        },
    ]


@pytest.mark.asyncio
async def test_super_admins_and_disabled_accounts_are_flagged(
    app, async_session_maker, roles
):
    home = await _add_org(async_session_maker, 'home')
    await _add_user(
        async_session_maker, 'root@x.example', home, role_id=roles['admin'].id
    )
    await _add_user(async_session_maker, 'gone@x.example', home, is_disabled=True)
    await _add_user(async_session_maker, 'plain@x.example', home)

    items = (await _get(app, '/api/admin/directory/users')).json()['items']

    assert {u['email']: (u['is_super_admin'], u['status']) for u in items} == {
        'gone@x.example': (False, 'inactive'),
        'plain@x.example': (False, 'active'),
        'root@x.example': (True, 'active'),
    }


@pytest.mark.asyncio
async def test_users_are_searched_by_email_name_or_organization(
    app, async_session_maker, roles
):
    acme = await _add_org(async_session_maker, 'Acme')
    globex = await _add_org(async_session_maker, 'Globex')
    ada = await _add_user(async_session_maker, 'ada@x.example', globex)
    await _add_user(
        async_session_maker, 'bob@x.example', globex, git_user_name='Robert Acme'
    )
    cy = await _add_user(async_session_maker, 'cy@x.example', globex)
    await _add_user(async_session_maker, 'dee@x.example', globex)
    await _add_member(async_session_maker, acme, cy, roles['member'])
    await _add_member(async_session_maker, globex, ada, roles['member'])

    by_org = (await _get(app, '/api/admin/directory/users', q='acme')).json()
    by_email = (await _get(app, '/api/admin/directory/users', q='dee@')).json()

    assert [u['email'] for u in by_org['items']] == ['bob@x.example', 'cy@x.example']
    assert by_org['total'] == 2
    assert [u['email'] for u in by_email['items']] == ['dee@x.example']


@pytest.mark.asyncio
async def test_pending_invitees_follow_the_accounts_across_the_same_offset(
    app, async_session_maker, roles
):
    acme = await _add_org(async_session_maker, 'Acme')
    globex = await _add_org(async_session_maker, 'Globex')
    inviter = await _add_user(async_session_maker, 'a@x.example', acme)
    await _add_user(async_session_maker, 'b@x.example', acme)
    await _invite(async_session_maker, acme, 'y@new.example', roles['member'], inviter)
    await _invite(async_session_maker, globex, 'y@new.example', roles['admin'], inviter)
    await _invite(async_session_maker, acme, 'x@new.example', roles['member'], inviter)

    first = (await _get(app, '/api/admin/directory/users', limit=3)).json()
    second = (
        await _get(app, '/api/admin/directory/users', limit=3, page_id='3')
    ).json()
    only_invitees = (
        await _get(app, '/api/admin/directory/users', limit=2, page_id='2')
    ).json()

    assert [u['email'] for u in first['items']] == [
        'a@x.example',
        'b@x.example',
        'x@new.example',
    ]
    assert [u['email'] for u in second['items']] == ['y@new.example']
    assert [u['email'] for u in only_invitees['items']] == [
        'x@new.example',
        'y@new.example',
    ]
    assert first['total'] == 4
    invitee = second['items'][0]
    assert invitee['user_id'] is None
    assert invitee['name'] == 'y'
    assert invitee['status'] == 'active'
    assert [
        (m['org_name'], m['role'], m['status']) for m in invitee['memberships']
    ] == [
        ('Acme', 'member', 'invited'),
        ('Globex', 'admin', 'invited'),
    ]


@pytest.mark.asyncio
async def test_only_live_invitations_to_unknown_addresses_are_listed(
    app, async_session_maker, roles
):
    acme = await _add_org(async_session_maker, 'Acme')
    member = await _add_user(async_session_maker, 'me@acme.example', acme)
    for email, kwargs in (
        ('ME@acme.example', {}),
        ('accepted@new.example', {'status': OrgInvitation.STATUS_ACCEPTED}),
        ('revoked@new.example', {'status': OrgInvitation.STATUS_REVOKED}),
        ('expired@new.example', {'expires_in': timedelta(days=-1)}),
        ('live@new.example', {}),
    ):
        await _invite(
            async_session_maker, acme, email, roles['member'], member, **kwargs
        )

    body = (await _get(app, '/api/admin/directory/users')).json()

    assert [u['email'] for u in body['items']] == [
        'me@acme.example',
        'live@new.example',
    ]
    assert body['total'] == 2


@pytest.mark.asyncio
async def test_invitees_are_searched_by_address_or_organization(
    app, async_session_maker, roles
):
    acme = await _add_org(async_session_maker, 'Acme')
    globex = await _add_org(async_session_maker, 'Globex')
    inviter = await _add_user(async_session_maker, 'a@x.example', acme)
    await _invite(
        async_session_maker, acme, 'one@new.example', roles['member'], inviter
    )
    await _invite(
        async_session_maker, globex, 'two@new.example', roles['member'], inviter
    )

    by_org = (await _get(app, '/api/admin/directory/users', q='globex')).json()
    by_email = (await _get(app, '/api/admin/directory/users', q='ONE@')).json()

    assert [u['email'] for u in by_org['items']] == ['two@new.example']
    assert [u['email'] for u in by_email['items']] == ['one@new.example']


# ── GET /api/admin/directory/users/{user_id} ───────────────────────────────


@pytest.mark.asyncio
async def test_one_user_is_read_with_their_memberships(app, async_session_maker, roles):
    acme = await _add_org(async_session_maker, 'Acme')
    ada = await _add_user(
        async_session_maker, 'ada@x.example', acme, role_id=roles['admin'].id
    )
    await _add_member(async_session_maker, acme, ada, roles['owner'])

    resp = await _get(app, f'/api/admin/directory/users/{ada.id}')

    assert resp.status_code == 200
    body = resp.json()
    assert body['user_id'] == str(ada.id)
    assert body['is_super_admin'] is True
    assert [(m['org_id'], m['role']) for m in body['memberships']] == [
        (str(acme.id), 'owner')
    ]


@pytest.mark.asyncio
async def test_an_unknown_user_is_not_found(app):
    resp = await _get(app, f'/api/admin/directory/users/{uuid.uuid4()}')

    assert resp.status_code == 404
