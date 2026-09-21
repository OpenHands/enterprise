"""Route-level tests for the org quota administration API.

These exercise the FastAPI route through ``require_permission`` and stub
the DB session at the route module boundary. Authorization is faked the
same way ``test_super_admins.py`` does: short-circuit the org-role lookup
to ``None`` and stack a ``superadmin`` super role on top of the
conftest-level ``get_user_super_role -> None`` default.
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.quota import quota_admin_router

CALLER_USER_ID = str(uuid.uuid4())
ORG_ID = uuid.uuid4()


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(quota_admin_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def grant_manage_org_quota():
    """Make ``MANAGE_ORG_QUOTA`` succeed by faking a ``superadmin`` role."""
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
def deny_manage_org_quota():
    """Caller is an org owner but holds no super role."""
    owner = MagicMock()
    owner.name = 'owner'
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=owner),
    ):
        yield


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _fake_org(limit: int | None = None):
    org = MagicMock()
    org.id = ORG_ID
    org.name = 'Acme'
    org.daily_conversation_limit = limit
    return org


@asynccontextmanager
async def _session_yielding(org):
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=org)
    yield session


def _patch_session(org):
    return patch(
        'server.routes.quota.a_session_maker',
        lambda **kwargs: _session_yielding(org),
    )


def _url(org_id=ORG_ID):
    return f'/api/admin/quota/orgs/{org_id}/quota'


@pytest.mark.asyncio
async def test_superadmin_can_set_org_limit(mock_app, grant_manage_org_quota):
    org = _fake_org()
    with _patch_session(org):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': 25})

    assert resp.status_code == 200
    assert resp.json() == {
        'org_id': str(ORG_ID),
        'org_name': 'Acme',
        'daily_conversation_limit': 25,
    }
    assert org.daily_conversation_limit == 25


@pytest.mark.asyncio
async def test_superadmin_can_exempt_org(mock_app, grant_manage_org_quota):
    org = _fake_org(limit=10)
    with _patch_session(org):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': -1})

    assert resp.status_code == 200
    assert resp.json()['daily_conversation_limit'] == -1


@pytest.mark.asyncio
async def test_superadmin_can_clear_override(mock_app, grant_manage_org_quota):
    org = _fake_org(limit=10)
    with _patch_session(org):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': None})

    assert resp.status_code == 200
    assert resp.json()['daily_conversation_limit'] is None


@pytest.mark.asyncio
async def test_unknown_org_returns_404(mock_app, grant_manage_org_quota):
    with _patch_session(None):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': 5})

    assert resp.status_code == 404
    assert resp.json()['detail'] == 'Organization not found'


@pytest.mark.asyncio
async def test_org_owner_without_super_role_is_denied(mock_app, deny_manage_org_quota):
    """MANAGE_ORG_QUOTA is instance-level: no org-scoped role grants it."""
    org = _fake_org()
    with _patch_session(org):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': 5})

    assert resp.status_code == 403
    assert org.daily_conversation_limit is None


@pytest.mark.asyncio
async def test_org_bound_api_key_cannot_edit_another_org(
    mock_app, grant_manage_org_quota
):
    """An API key bound to one org must not reach another org's quota.

    Regression guard for the hand-rolled super-role check this route used
    to carry, which skipped ``require_permission``'s API-key organization
    binding entirely.
    """
    other_org = uuid.uuid4()
    org = _fake_org()
    with (
        patch(
            'server.auth.authorization.get_api_key_org_id_from_request',
            AsyncMock(return_value=other_org),
        ),
        _patch_session(org),
    ):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': 5})

    assert resp.status_code == 403
    assert 'not authorized for this organization' in resp.json()['detail']
    assert org.daily_conversation_limit is None


@pytest.mark.asyncio
async def test_org_bound_api_key_can_edit_its_own_org(mock_app, grant_manage_org_quota):
    org = _fake_org()
    with (
        patch(
            'server.auth.authorization.get_api_key_org_id_from_request',
            AsyncMock(return_value=ORG_ID),
        ),
        _patch_session(org),
    ):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': 5})

    assert resp.status_code == 200
    assert org.daily_conversation_limit == 5


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [0, -2, -11, -100])
async def test_meaningless_limits_are_rejected(mock_app, grant_manage_org_quota, limit):
    """0 and values below -1 would silently hard-block the whole org."""
    org = _fake_org()
    with _patch_session(org):
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': limit})

    assert resp.status_code == 422
    assert org.daily_conversation_limit is None


@pytest.mark.asyncio
async def test_malformed_org_id_is_rejected(mock_app, grant_manage_org_quota):
    """A non-UUID org id is a 422, not an unhandled 500."""
    async with _client(mock_app) as client:
        resp = await client.put(
            _url('not-a-uuid'), json={'daily_conversation_limit': 5}
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_conflicting_x_org_id_header_is_rejected(
    mock_app, grant_manage_org_quota
):
    """The path pins the org; a contradictory X-Org-Id header is a 400."""
    org = _fake_org()
    with _patch_session(org):
        async with _client(mock_app) as client:
            resp = await client.put(
                _url(),
                json={'daily_conversation_limit': 5},
                headers={'X-Org-Id': str(uuid.uuid4())},
            )

    assert resp.status_code == 400
    assert org.daily_conversation_limit is None


@pytest.mark.asyncio
async def test_successful_change_is_logged_with_the_caller(
    mock_app, grant_manage_org_quota
):
    """The mutation is attributable: who changed which org's quota, to what.

    Exempting an org is revenue-affecting, so an unattributable write is
    not good enough -- this mirrors ``super_admins:grant`` / ``:revoke``.
    """
    org = _fake_org()
    with _patch_session(org), patch('server.routes.quota.logger') as log:
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': -1})

    assert resp.status_code == 200
    log.info.assert_called_once()
    event, kwargs = log.info.call_args[0][0], log.info.call_args[1]
    assert event == 'org_quota:set'
    assert kwargs['extra'] == {
        'caller_user_id': CALLER_USER_ID,
        'org_id': str(ORG_ID),
        'daily_conversation_limit': -1,
        'exempt': True,
    }


@pytest.mark.asyncio
async def test_denied_request_logs_no_change(mock_app, deny_manage_org_quota):
    """A rejected call must not leave a record implying a change happened."""
    org = _fake_org()
    with _patch_session(org), patch('server.routes.quota.logger') as log:
        async with _client(mock_app) as client:
            resp = await client.put(_url(), json={'daily_conversation_limit': 5})

    assert resp.status_code == 403
    log.info.assert_not_called()
