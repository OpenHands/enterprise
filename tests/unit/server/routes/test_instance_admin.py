"""Route-level tests for the Super Admin instance directory APIs."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.instance_admin import instance_admin_router
from server.routes.org_models import OrgNotFoundError

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


@pytest.fixture
def deny_manage_super_admins():
    """Caller has neither an org role nor a super role."""
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=None),
        ),
    ):
        yield


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_list_organizations_requires_super_admin(
    mock_app, deny_manage_super_admins
):
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
    with (
        patch(
            'server.routes.instance_admin._is_personal_workspace',
            AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.instance_admin.OrgService.delete_org_with_cleanup',
            AsyncMock(side_effect=OrgNotFoundError(str(org_id))),
        ),
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
    with (
        patch(
            'server.routes.instance_admin._is_personal_workspace',
            AsyncMock(return_value=False),
        ),
        patch(
            'server.routes.instance_admin.OrgService.delete_org_with_cleanup',
            AsyncMock(return_value=deleted),
        ) as delete_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/organizations/{org_id}')

    assert resp.status_code == 200
    delete_mock.assert_awaited_once()
    assert delete_mock.await_args.kwargs.get('allow_super_admin') is True


@pytest.mark.asyncio
async def test_delete_organization_rejects_personal_workspace(
    mock_app, grant_manage_super_admins
):
    personal_org_id = uuid.UUID(CALLER_USER_ID)
    delete_org = AsyncMock()
    with (
        patch(
            'server.routes.instance_admin._is_personal_workspace',
            AsyncMock(return_value=True),
        ),
        patch(
            'server.routes.instance_admin.OrgService.delete_org_with_cleanup',
            delete_org,
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/organizations/{personal_org_id}')

    assert resp.status_code == 403
    delete_org.assert_not_awaited()


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
            'server.routes.instance_admin._is_personal_workspace',
            AsyncMock(return_value=False),
        ),
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
async def test_update_organization_status_rejects_suspending_personal_workspace(
    mock_app, grant_manage_super_admins
):
    personal_org_id = uuid.uuid4()
    set_status = AsyncMock()
    with (
        patch(
            'server.routes.instance_admin._is_personal_workspace',
            AsyncMock(return_value=True),
        ),
        patch('server.routes.instance_admin.OrgStore.set_org_status', set_status),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                f'/api/admin/organizations/{personal_org_id}',
                json={'status': 'suspended'},
            )

    assert resp.status_code == 403
    set_status.assert_not_awaited()


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
async def test_update_user_status_rejects_suspending_self(
    mock_app, grant_manage_super_admins
):
    caller = MagicMock()
    caller.id = uuid.UUID(CALLER_USER_ID)
    caller.email = 'admin@acme.example'
    caller.git_user_name = 'Admin'
    set_status = AsyncMock()

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=caller),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.set_all_membership_statuses',
            set_status,
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.list_memberships_with_orgs',
            AsyncMock(return_value=[]),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                f'/api/admin/users/{CALLER_USER_ID}',
                json={'status': 'inactive'},
            )

    assert resp.status_code == 403
    set_status.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_remove_user_rejects_self(mock_app, grant_manage_super_admins):
    caller = MagicMock()
    caller.id = uuid.UUID(CALLER_USER_ID)

    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = 'Acme'
    member = MagicMock()
    member.role_id = 1
    role = MagicMock()
    role.name = 'member'
    remove_member = AsyncMock(return_value=True)

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=caller),
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
            'server.routes.instance_admin.OrgMemberStore.remove_user_from_org',
            remove_member,
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/users/{CALLER_USER_ID}')

    assert resp.status_code == 403
    remove_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_user_resets_current_org_and_leaves_litellm_team(
    mock_app, grant_manage_super_admins
):
    user_id = uuid.uuid4()
    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = 'Acme'
    user = MagicMock()
    user.id = user_id
    user.current_org_id = org.id
    member = MagicMock()
    member.role_id = 1
    role = MagicMock()
    role.name = 'member'
    update_current_org = AsyncMock()
    remove_from_team = AsyncMock()

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
            'server.services.org_member_service.OrgMemberStore.remove_user_from_org',
            AsyncMock(return_value=True),
        ),
        patch(
            'server.services.org_member_service.UserStore.update_current_org',
            update_current_org,
        ),
        patch(
            'server.services.org_member_service.LiteLlmManager.remove_user_from_team',
            remove_from_team,
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/users/{user_id}')

    assert resp.status_code == 200
    update_current_org.assert_awaited_once_with(str(user_id), user_id)
    remove_from_team.assert_awaited_once_with(str(user_id), str(org.id))


@pytest.mark.asyncio
async def test_update_user_groups_suspends_selected_orgs(
    mock_app, grant_manage_super_admins
):
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
    set_status = AsyncMock(return_value=1)

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.set_all_membership_statuses',
            set_status,
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
            resp = await client.post(
                f'/api/admin/users/{user_id}/groups',
                json={'action': 'suspend', 'org_ids': [str(org.id)]},
            )

    assert resp.status_code == 200
    set_status.assert_awaited_once_with(user_id, 'inactive', [org.id])


@pytest.mark.asyncio
async def test_update_user_groups_remove_blocks_last_owner(
    mock_app, grant_manage_super_admins
):
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
            resp = await client.post(
                f'/api/admin/users/{user_id}/groups',
                json={'action': 'remove', 'org_ids': [str(org.id)]},
            )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_user_groups_remove_resets_current_org_and_leaves_litellm_team(
    mock_app, grant_manage_super_admins
):
    user_id = uuid.uuid4()
    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = 'Acme'
    user = MagicMock()
    user.id = user_id
    user.email = 'alex@acme.example'
    user.git_user_name = 'Alex'
    user.current_org_id = org.id
    member = MagicMock()
    member.role_id = 1
    member.status = 'active'
    role = MagicMock()
    role.name = 'member'
    update_current_org = AsyncMock()
    remove_from_team = AsyncMock()

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
            'server.services.org_member_service.OrgMemberStore.remove_user_from_org',
            AsyncMock(return_value=True),
        ),
        patch(
            'server.services.org_member_service.UserStore.update_current_org',
            update_current_org,
        ),
        patch(
            'server.services.org_member_service.LiteLlmManager.remove_user_from_team',
            remove_from_team,
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                f'/api/admin/users/{user_id}/groups',
                json={'action': 'remove', 'org_ids': [str(org.id)]},
            )

    assert resp.status_code == 200
    update_current_org.assert_awaited_once_with(str(user_id), user_id)
    remove_from_team.assert_awaited_once_with(str(user_id), str(org.id))


@pytest.mark.asyncio
async def test_update_user_groups_add_rejects_another_users_personal_workspace(
    mock_app, grant_manage_super_admins
):
    caller = MagicMock()
    caller.id = uuid.UUID(CALLER_USER_ID)
    other_users_personal_org_id = uuid.uuid4()
    add_member = AsyncMock()

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=caller),
        ),
        patch(
            'server.routes.instance_admin.RoleStore.get_role_by_name',
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            'server.routes.instance_admin._is_personal_workspace',
            AsyncMock(return_value=True),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.add_user_to_org',
            add_member,
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                f'/api/admin/users/{CALLER_USER_ID}/groups',
                json={
                    'action': 'add',
                    'org_ids': [str(other_users_personal_org_id)],
                    'role': 'admin',
                },
            )

    assert resp.status_code == 403
    add_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_user_groups_remove_rejects_own_personal_workspace(
    mock_app, grant_manage_super_admins
):
    user_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.email = 'alex@acme.example'
    user.git_user_name = 'Alex'

    personal_org = MagicMock()
    personal_org.id = user_id
    personal_org.name = f'user_{user_id}_org'
    member = MagicMock()
    member.role_id = 1
    member.status = 'active'
    role = MagicMock()
    role.name = 'owner'
    remove_member = AsyncMock(return_value=True)

    with (
        patch(
            'server.routes.instance_admin.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.list_memberships_with_orgs',
            AsyncMock(return_value=[(member, personal_org)]),
        ),
        patch(
            'server.routes.instance_admin.RoleStore.get_role_by_id',
            AsyncMock(return_value=role),
        ),
        patch(
            'server.routes.instance_admin.OrgMemberStore.remove_user_from_org',
            remove_member,
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                f'/api/admin/users/{user_id}/groups',
                json={'action': 'remove', 'org_ids': [str(user_id)]},
            )

    assert resp.status_code == 403
    remove_member.assert_not_awaited()
