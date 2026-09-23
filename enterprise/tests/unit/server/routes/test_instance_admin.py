"""Route-level tests for the Super Admin instance directory APIs."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from server.routes.instance_admin import instance_admin_router
from server.routes.org_models import OrgNotFoundError

from openhands.app_server.user_auth import get_user_id

CALLER_USER_ID = str(uuid.uuid4())


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(instance_admin_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def grant_manage_super_admins():
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
    ):
        yield


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_list_organizations_requires_super_admin(mock_app):
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/organizations')
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_organizations_success(mock_app, grant_manage_super_admins):
    org_id = uuid.uuid4()
    org = MagicMock()
    org.id = org_id
    org.name = 'Acme'
    org.contact_email = 'ops@acme.example'
    org.contact_name = 'Ops'
    org.status = 'active'

    fake_result = MagicMock()
    fake_result.all.return_value = [(org, 3)]
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=fake_result)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    empty_users = MagicMock()
    empty_users.scalars.return_value.all.return_value = []
    fake_session_users = MagicMock()
    fake_session_users.execute = AsyncMock(return_value=empty_users)
    fake_session_users.__aenter__ = AsyncMock(return_value=fake_session_users)
    fake_session_users.__aexit__ = AsyncMock(return_value=None)

    with patch(
        'server.routes.instance_admin.a_session_maker',
        side_effect=[fake_session, fake_session_users],
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/organizations')

    assert resp.status_code == 200
    body = resp.json()
    assert len(body['organizations']) == 1
    assert body['organizations'][0]['name'] == 'Acme'
    assert body['organizations'][0]['member_count'] == 3


@pytest.mark.asyncio
async def test_list_users_success(mock_app, grant_manage_super_admins):
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.email = 'me@acme.example'
    user.git_user_name = 'openhands'

    org_member = MagicMock()
    org_member.user_id = user_id
    org_member.status = 'active'
    org = MagicMock()
    org.id = org_id
    org.name = 'Acme'
    role = MagicMock()
    role.name = 'owner'

    users_result = MagicMock()
    users_result.scalars.return_value = [user]
    membership_result = MagicMock()
    membership_result.all.return_value = [(org_member, org, role)]

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[users_result, membership_result])
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        'server.routes.instance_admin.a_session_maker',
        return_value=fake_session,
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/users')

    assert resp.status_code == 200
    body = resp.json()
    assert len(body['users']) == 1
    assert body['users'][0]['email'] == 'me@acme.example'
    assert body['users'][0]['memberships'][0]['role'] == 'owner'


@pytest.mark.asyncio
async def test_delete_organization_not_found(mock_app, grant_manage_super_admins):
    org_id = uuid.uuid4()
    with patch(
        'server.routes.instance_admin.OrgService.delete_org_with_cleanup',
        AsyncMock(side_effect=OrgNotFoundError(str(org_id))),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/organizations/{org_id}')
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_organization_success(mock_app, grant_manage_super_admins):
    org_id = uuid.uuid4()
    deleted = MagicMock()
    deleted.id = org_id
    deleted.name = 'Gone'
    deleted.contact_name = None
    deleted.contact_email = None
    with patch(
        'server.routes.instance_admin.OrgService.delete_org_with_cleanup',
        AsyncMock(return_value=deleted),
    ) as delete_mock:
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/organizations/{org_id}')

    assert resp.status_code == 200
    delete_mock.assert_awaited_once()
    assert delete_mock.await_args.kwargs.get('allow_super_admin') is True


@pytest.mark.asyncio
async def test_update_organization_status_success(mock_app, grant_manage_super_admins):
    org_id = uuid.uuid4()
    org = MagicMock()
    org.id = org_id
    org.name = 'Acme'
    org.contact_email = 'ops@acme.example'
    org.contact_name = 'Ops'
    org.status = 'suspended'
    with (
        patch(
            'server.routes.instance_admin.OrgStore.set_org_status',
            AsyncMock(return_value=org),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.get_org_members_count',
            AsyncMock(return_value=3),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                f'/api/admin/organizations/{org_id}',
                json={'status': 'suspended'},
            )

    assert resp.status_code == 200
    assert resp.json()['status'] == 'suspended'


@pytest.mark.asyncio
async def test_update_user_status_success(mock_app, grant_manage_super_admins):
    user_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.email = 'alex@acme.example'
    user.git_user_name = 'Alex'

    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = 'Acme'
    member = MagicMock()
    member.role_id = 1
    member.status = 'inactive'
    role = MagicMock()
    role.name = 'member'

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.set_all_membership_statuses',
            AsyncMock(return_value=1),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.list_memberships_with_orgs',
            AsyncMock(return_value=[(member, org)]),
        ),
        patch(
            'server.routes.instance_admin.RoleStore.get_role_by_id',
            AsyncMock(return_value=role),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                f'/api/admin/users/{user_id}',
                json={'status': 'inactive'},
            )

    assert resp.status_code == 200
    assert resp.json()['status'] == 'inactive'


@pytest.mark.asyncio
async def test_remove_user_blocks_last_owner(mock_app, grant_manage_super_admins):
    user_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.email = 'owner@acme.example'
    user.git_user_name = 'Owner'

    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = 'Acme'
    member = MagicMock()
    member.role_id = 1
    role = MagicMock()
    role.name = 'owner'

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.list_memberships_with_orgs',
            AsyncMock(return_value=[(member, org)]),
        ),
        patch(
            'server.routes.instance_admin.RoleStore.get_role_by_id',
            AsyncMock(return_value=role),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberService._is_last_owner',
            AsyncMock(return_value=True),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/users/{user_id}')

    assert resp.status_code == 409
