"""Route-level tests for the super-admin dashboard API.

Authorization is faked the same way ``test_super_admins.py`` does: short-circuit
the org-role lookup to ``None`` and stack a ``superadmin`` super role on top of
the conftest-level ``get_user_super_role -> None`` default.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from server.routes.admin_dashboard import admin_dashboard_router

from openhands.app_server.user_auth import get_user_id

CALLER_USER_ID = str(uuid.uuid4())


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(admin_dashboard_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def as_super_admin():
    """Make super-role permission checks succeed by faking a ``superadmin``."""
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
        # The status route resolves the super role via the admin_dashboard
        # module import, so patch it there too.
        patch(
            'server.routes.admin_dashboard.get_user_super_role',
            AsyncMock(return_value=superadmin),
        ),
    ):
        yield


@pytest.fixture
def as_regular_user():
    """No org role and no super role -> permission checks fail."""
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.routes.admin_dashboard.get_user_super_role',
            AsyncMock(return_value=None),
        ),
    ):
        yield


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _org(name: str, is_default: bool = False):
    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = name
    org.contact_email = f'{name}@example.com'
    org.is_default = is_default
    return org


@pytest.mark.asyncio
async def test_list_orgs_returns_sorted_team_orgs(mock_app, as_super_admin):
    orgs = [_org('Zeta'), _org('alpha'), _org('Default', is_default=True)]
    with patch(
        'server.routes.admin_dashboard.OrgStore.list_team_orgs',
        AsyncMock(return_value=orgs),
    ) as list_mock:
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/orgs')

    assert resp.status_code == 200
    list_mock.assert_awaited_once()
    names = [o['name'] for o in resp.json()['organizations']]
    # Case-insensitive sort by name.
    assert names == ['alpha', 'Default', 'Zeta']
    default_row = next(
        o for o in resp.json()['organizations'] if o['name'] == 'Default'
    )
    assert default_row['is_default'] is True
    assert default_row['contact_email'] == 'Default@example.com'


@pytest.mark.asyncio
async def test_list_orgs_forbidden_for_regular_user(mock_app, as_regular_user):
    with patch(
        'server.routes.admin_dashboard.OrgStore.list_team_orgs',
        AsyncMock(return_value=[]),
    ) as list_mock:
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/orgs')

    assert resp.status_code == 403
    list_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_super_admin_status_true_for_super_admin(mock_app, as_super_admin):
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/super-admin-status')

    assert resp.status_code == 200
    assert resp.json() == {'is_super_admin': True}


@pytest.mark.asyncio
async def test_super_admin_status_false_for_regular_user(mock_app, as_regular_user):
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/super-admin-status')

    # Not permission-gated: returns 200 with is_super_admin=False so the
    # frontend can cheaply decide whether to render the dashboard nav entry.
    assert resp.status_code == 200
    assert resp.json() == {'is_super_admin': False}
