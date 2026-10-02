"""Route-level tests for the super-admin management API.

These exercise the FastAPI routes through ``require_permission`` and stub
``UserStore`` at the route module boundary. Authorization itself is faked the
same way ``test_orgs.py`` fakes ``CREATE_ORGANIZATION``: short-circuit the
org-role lookup to ``None`` and stack a ``superadmin`` super role on top of the
conftest-level ``get_user_super_role -> None`` default.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.super_admins import super_admin_router
from storage.user_store import SuperAdminRevokeResult

CALLER_USER_ID = str(uuid.uuid4())


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(super_admin_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def grant_manage_super_admins():
    """Make ``MANAGE_SUPER_ADMINS`` succeed by faking a ``superadmin`` role."""
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


def _fake_user(user_id: str, email: str | None = None):
    user = MagicMock()
    user.id = uuid.UUID(user_id)
    user.email = email
    return user


@pytest.mark.asyncio
async def test_grant_by_user_id_success(mock_app, grant_manage_super_admins):
    target = str(uuid.uuid4())
    with patch(
        'server.routes.super_admins.UserStore.grant_super_admin',
        AsyncMock(return_value=_fake_user(target, 'new@example.com')),
    ) as grant_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/super-admins', json={'user_id': target}
            )

    assert resp.status_code == 201
    assert resp.json()['user_id'] == target
    grant_mock.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_grant_by_email_resolves_user(mock_app, grant_manage_super_admins):
    target = str(uuid.uuid4())
    with (
        patch(
            'server.routes.super_admins.UserStore.get_user_by_email',
            AsyncMock(return_value=_fake_user(target, 'someone@example.com')),
        ),
        patch(
            'server.routes.super_admins.UserStore.grant_super_admin',
            AsyncMock(return_value=_fake_user(target, 'someone@example.com')),
        ) as grant_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/super-admins', json={'email': 'someone@example.com'}
            )

    assert resp.status_code == 201
    grant_mock.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_grant_requires_exactly_one_identifier(
    mock_app, grant_manage_super_admins
):
    async with _client(mock_app) as client:
        both = await client.post(
            '/api/admin/super-admins',
            json={'user_id': str(uuid.uuid4()), 'email': 'x@example.com'},
        )
        neither = await client.post('/api/admin/super-admins', json={})

    assert both.status_code == 422
    assert neither.status_code == 422


@pytest.mark.asyncio
async def test_grant_user_not_found(mock_app, grant_manage_super_admins):
    with patch(
        'server.routes.super_admins.UserStore.grant_super_admin',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/super-admins', json={'user_id': str(uuid.uuid4())}
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_grant_by_email_unknown(mock_app, grant_manage_super_admins):
    with patch(
        'server.routes.super_admins.UserStore.get_user_by_email',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/super-admins', json={'email': 'ghost@example.com'}
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoke_success(mock_app, grant_manage_super_admins):
    target = str(uuid.uuid4())
    with patch(
        'server.routes.super_admins.UserStore.revoke_super_admin',
        AsyncMock(return_value=SuperAdminRevokeResult.REVOKED),
    ) as revoke_mock:
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/super-admins/{target}')

    assert resp.status_code == 200
    assert resp.json()['user_id'] == target
    revoke_mock.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_revoke_self_success(mock_app, grant_manage_super_admins):
    """Caller revoking their own id succeeds when another super admin exists."""
    with patch(
        'server.routes.super_admins.UserStore.revoke_super_admin',
        AsyncMock(return_value=SuperAdminRevokeResult.REVOKED),
    ) as revoke_mock:
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/super-admins/{CALLER_USER_ID}')

    assert resp.status_code == 200
    revoke_mock.assert_awaited_once_with(CALLER_USER_ID)


@pytest.mark.asyncio
async def test_revoke_last_super_admin_conflict(mock_app, grant_manage_super_admins):
    with patch(
        'server.routes.super_admins.UserStore.revoke_super_admin',
        AsyncMock(return_value=SuperAdminRevokeResult.LAST_SUPER_ADMIN),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/super-admins/{uuid.uuid4()}')

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_revoke_not_super_admin_404(mock_app, grant_manage_super_admins):
    with patch(
        'server.routes.super_admins.UserStore.revoke_super_admin',
        AsyncMock(return_value=SuperAdminRevokeResult.NOT_SUPER_ADMIN),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/super-admins/{uuid.uuid4()}')

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoke_unknown_user_404(mock_app, grant_manage_super_admins):
    with patch(
        'server.routes.super_admins.UserStore.revoke_super_admin',
        AsyncMock(return_value=SuperAdminRevokeResult.NOT_FOUND),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete(f'/api/admin/super-admins/{uuid.uuid4()}')

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_super_admins(mock_app, grant_manage_super_admins):
    """Flag off (default): superadmin can list -- unchanged behavior."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with (
        patch(
            'server.routes.super_admins.USER_PROVISIONING_ENABLED',
            False,
        ),
        patch(
            'server.routes.super_admins.UserStore.list_super_admins',
            AsyncMock(
                return_value=[_fake_user(a, 'a@x.com'), _fake_user(b, 'b@x.com')]
            ),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/super-admins')

    assert resp.status_code == 200
    ids = [s['user_id'] for s in resp.json()['super_admins']]
    assert ids == [a, b]


@pytest.mark.asyncio
async def test_manage_super_admins_forbidden_without_permission(mock_app):
    """Flag off (default): without a super role, access is denied (403)."""
    # No ``grant_manage_super_admins`` fixture here: org-role lookup must also
    # resolve to None so the permission check falls through to a denial.
    with (
        patch('server.routes.super_admins.USER_PROVISIONING_ENABLED', False),
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/super-admins')

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_super_admins_open_to_any_user_when_provisioning_enabled(
    mock_app,
):
    """Flag on (OHE-3196): any authenticated user can list super-admins.

    No super role is granted and the org-role lookup is left at the
    conftest default -- the flag-on path bypasses the permission check
    entirely and only requires a logged-in user.
    """
    a = str(uuid.uuid4())
    with (
        patch('server.routes.super_admins.USER_PROVISIONING_ENABLED', True),
        patch(
            'server.routes.super_admins.UserStore.list_super_admins',
            AsyncMock(return_value=[_fake_user(a, 'admin@example.com')]),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/super-admins')

    assert resp.status_code == 200
    assert resp.json()['super_admins'] == [{'user_id': a, 'email': 'admin@example.com'}]


@pytest.mark.asyncio
async def test_list_super_admins_requires_auth_when_provisioning_enabled(
    monkeypatch, mock_app
):
    """Flag on: an unauthenticated user gets 401, not the list."""
    monkeypatch.setattr('server.routes.super_admins.USER_PROVISIONING_ENABLED', True)
    # Override the ``get_user_id`` dependency to simulate no session.
    mock_app.dependency_overrides[get_user_id] = lambda: None
    with patch(
        'server.routes.super_admins.UserStore.list_super_admins',
        AsyncMock(return_value=[]),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/super-admins')

    assert resp.status_code == 401
