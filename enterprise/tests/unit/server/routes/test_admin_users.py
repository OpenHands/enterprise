"""Route tests for instance-level user lifecycle administration."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from server.routes.admin_users import admin_user_router

from openhands.app_server.user_auth import get_user_id

CALLER_USER_ID = str(uuid.uuid4())


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(admin_user_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def grant_manage_users():
    superadmin = MagicMock(name='admin')
    superadmin.name = 'admin'
    with (
        patch(
            'server.auth.authorization.get_user_org_role', AsyncMock(return_value=None)
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=superadmin),
        ),
        patch(
            'server.auth.authorization.get_user_id',
            AsyncMock(return_value=CALLER_USER_ID),
        ),
    ):
        yield


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _result(user_id: str):
    result = MagicMock()
    result.user_id = user_id
    result.email = 'user@example.com'
    result.cleanup_warnings = []
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'action,method,path',
    [
        ('disable_user', 'post', 'disable'),
        ('enable_user', 'post', 'enable'),
        ('delete_user', 'delete', ''),
    ],
)
async def test_lifecycle_success(mock_app, grant_manage_users, action, method, path):
    target = str(uuid.uuid4())
    with patch(
        f'server.routes.admin_users.AdminUserLifecycleService.{action}',
        AsyncMock(return_value=_result(target)),
    ) as lifecycle:
        async with _client(mock_app) as client:
            response = await getattr(client, method)(
                f'/api/admin/users/{target}/{path}'.rstrip('/')
            )

    assert response.status_code == 200
    assert response.json()['user_id'] == target
    assert response.json()['email'] == 'user@example.com'
    assert response.json()['warnings'] == []
    lifecycle.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_delete_user_surfaces_cleanup_warnings(mock_app, grant_manage_users):
    target = str(uuid.uuid4())
    result = _result(target)
    result.cleanup_warnings = ['LiteLLM cleanup failed', 'Keycloak deletion failed']
    with patch(
        'server.routes.admin_users.AdminUserLifecycleService.delete_user',
        AsyncMock(return_value=result),
    ):
        async with _client(mock_app) as client:
            response = await client.delete(f'/api/admin/users/{target}')

    assert response.status_code == 200
    assert response.json()['warnings'] == [
        'LiteLLM cleanup failed',
        'Keycloak deletion failed',
    ]


@pytest.mark.asyncio
async def test_lifecycle_requires_permission(mock_app):
    target = str(uuid.uuid4())
    with patch(
        'server.auth.authorization.get_user_org_role', AsyncMock(return_value=None)
    ):
        async with _client(mock_app) as client:
            response = await client.post(f'/api/admin/users/{target}/disable')

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_lifecycle_unknown_user(mock_app, grant_manage_users):
    with patch(
        'server.routes.admin_users.AdminUserLifecycleService.disable_user',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            response = await client.post(f'/api/admin/users/{uuid.uuid4()}/disable')

    assert response.status_code == 404
