"""Route-level tests for the Super Admin instance directory APIs."""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from openhands.app_server.user_auth import get_user_id
from server.routes.instance_admin import MAX_LOGO_BYTES, instance_admin_router
from server.routes.org_models import OrgNotFoundError
from storage.instance_settings import InstanceSettings
from storage.org import Org

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


LOGO = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()


@pytest.fixture
def instance_settings_db(async_session_maker):
    with patch('server.routes.instance_admin.a_session_maker', async_session_maker):
        yield


def _app_for(user_id: str | None) -> FastAPI:
    app = FastAPI()
    app.include_router(instance_admin_router)
    app.dependency_overrides[get_user_id] = lambda: user_id
    return app


@pytest.mark.asyncio
async def test_get_instance_settings_requires_sign_in(instance_settings_db):
    # Arrange
    app = _app_for(None)

    # Act
    async with _client(app) as client:
        resp = await client.get('/api/admin/instance-settings')

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_instance_settings_is_empty_before_anything_is_saved(
    mock_app, instance_settings_db
):
    # Act
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/instance-settings')

    # Assert
    assert resp.status_code == 200
    assert resp.json() == {'company_name': None, 'logo': None}


@pytest.mark.asyncio
async def test_saved_instance_settings_are_visible_to_other_signed_in_users(
    mock_app, grant_manage_super_admins, instance_settings_db
):
    # Arrange
    async with _client(mock_app) as client:
        await client.patch(
            '/api/admin/instance-settings',
            json={'company_name': 'Acme', 'logo': LOGO},
        )

    # Act
    async with _client(_app_for(str(uuid.uuid4()))) as client:
        resp = await client.get('/api/admin/instance-settings')

    # Assert
    assert resp.status_code == 200
    assert resp.json() == {'company_name': 'Acme', 'logo': LOGO}


@pytest.mark.asyncio
async def test_update_instance_settings_requires_super_admin(
    mock_app, deny_manage_super_admins, instance_settings_db
):
    # Act
    async with _client(mock_app) as client:
        resp = await client.patch('/api/admin/instance-settings', json={'logo': LOGO})
        saved = await client.get('/api/admin/instance-settings')

    # Assert
    assert resp.status_code == 403
    assert saved.json() == {'company_name': None, 'logo': None}


@pytest.mark.asyncio
async def test_update_instance_settings_keeps_omitted_fields_and_clears_null_ones(
    mock_app, grant_manage_super_admins, instance_settings_db
):
    # Arrange
    async with _client(mock_app) as client:
        await client.patch(
            '/api/admin/instance-settings',
            json={'company_name': 'Acme', 'logo': LOGO},
        )

        # Act
        resp = await client.patch('/api/admin/instance-settings', json={'logo': None})

    # Assert
    assert resp.status_code == 200
    assert resp.json() == {'company_name': 'Acme', 'logo': None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        pytest.param(
            {
                'logo': 'data:image/svg+xml;base64,'
                + base64.b64encode(b'<svg/>').decode()
            },
            id='svg-logo',
        ),
        pytest.param({'logo': 'https://example.com/logo.png'}, id='remote-logo'),
        pytest.param({'logo': 'data:image/png;base64,not-base64!'}, id='bad-base64'),
        pytest.param(
            {
                'logo': 'data:image/png;base64,'
                + base64.b64encode(b'x' * (MAX_LOGO_BYTES + 1)).decode()
            },
            id='oversized-logo',
        ),
        pytest.param({'company_name': 'x' * 256}, id='long-company-name'),
    ],
)
async def test_update_instance_settings_rejects_invalid_values(
    body, mock_app, grant_manage_super_admins
):
    # Act
    async with _client(mock_app) as client:
        resp = await client.patch('/api/admin/instance-settings', json=body)

    # Assert
    assert resp.status_code == 422


NO_SETUP_STATE = {
    'wizard_pending': False,
    'guide_org_id': None,
    'guide_dismissed': False,
}


async def _record_setup_user(async_session_maker, user_id: str) -> None:
    async with async_session_maker() as session:
        session.add(InstanceSettings(id=1, setup_user_id=uuid.UUID(user_id)))
        await session.commit()


async def _add_org(async_session_maker) -> uuid.UUID:
    org_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Org(id=org_id, name=f'org-{org_id}'))
        await session.commit()
    return org_id


@pytest.mark.asyncio
async def test_get_setup_state_requires_sign_in(instance_settings_db):
    # Arrange
    app = _app_for(None)

    # Act
    async with _client(app) as client:
        resp = await client.get('/api/admin/setup-state')

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_setup_state_is_empty_when_no_first_super_admin_was_recorded(
    mock_app, instance_settings_db
):
    # Act
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/setup-state')

    # Assert
    assert resp.status_code == 200
    assert resp.json() == NO_SETUP_STATE


@pytest.mark.asyncio
async def test_first_super_admin_has_a_pending_wizard(
    mock_app, instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)

    # Act
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/setup-state')

    # Assert
    assert resp.status_code == 200
    assert resp.json() == {**NO_SETUP_STATE, 'wizard_pending': True}


@pytest.mark.asyncio
async def test_later_super_admin_is_not_reported_as_needing_the_wizard(
    instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)

    # Act
    async with _client(_app_for(str(uuid.uuid4()))) as client:
        resp = await client.get('/api/admin/setup-state')

    # Assert
    assert resp.status_code == 200
    assert resp.json() == NO_SETUP_STATE


@pytest.mark.asyncio
async def test_completed_wizard_is_no_longer_pending(
    mock_app, instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)

    async with _client(mock_app) as client:
        # Act
        resp = await client.patch(
            '/api/admin/setup-state', json={'wizard_completed': True}
        )
        saved = await client.get('/api/admin/setup-state')

    # Assert
    assert resp.status_code == 200
    assert saved.json() == NO_SETUP_STATE


@pytest.mark.asyncio
async def test_update_setup_state_saves_the_guide_org_and_dismissal(
    mock_app, instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)
    org_id = await _add_org(async_session_maker)

    # Act
    async with _client(mock_app) as client:
        resp = await client.patch(
            '/api/admin/setup-state',
            json={'guide_org_id': str(org_id), 'guide_dismissed': True},
        )

    # Assert
    assert resp.status_code == 200
    assert resp.json() == {
        'wizard_pending': True,
        'guide_org_id': str(org_id),
        'guide_dismissed': True,
    }


@pytest.mark.asyncio
async def test_only_the_first_super_admin_can_update_the_setup_state(
    mock_app, instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)

    # Act
    async with _client(_app_for(str(uuid.uuid4()))) as client:
        resp = await client.patch(
            '/api/admin/setup-state', json={'wizard_completed': True}
        )

    # Assert
    assert resp.status_code == 403
    async with _client(mock_app) as client:
        saved = await client.get('/api/admin/setup-state')
    assert saved.json()['wizard_pending'] is True


@pytest.mark.asyncio
async def test_update_setup_state_rejects_an_unknown_organization(
    mock_app, instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)

    # Act
    async with _client(mock_app) as client:
        resp = await client.patch(
            '/api/admin/setup-state', json={'guide_org_id': str(uuid.uuid4())}
        )

    # Assert
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deleting_the_guide_organization_clears_it_from_the_setup_state(
    mock_app, instance_settings_db, async_session_maker
):
    # Arrange
    await _record_setup_user(async_session_maker, CALLER_USER_ID)
    org_id = await _add_org(async_session_maker)
    async with _client(mock_app) as client:
        await client.patch('/api/admin/setup-state', json={'guide_org_id': str(org_id)})

    # Act
    async with async_session_maker() as session:
        await session.execute(text('DELETE FROM org WHERE id = :id'), {'id': org_id})
        await session.commit()

    # Assert
    async with _client(mock_app) as client:
        saved = await client.get('/api/admin/setup-state')
    assert saved.json()['guide_org_id'] is None
