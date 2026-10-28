"""User quota routes exercised against real PostgreSQL and authorization stores."""

import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from openhands.app_server.user_auth import get_user_id
from server.routes.quota import quota_admin_router
from server.services.daily_sandbox_quota_service import DailySandboxQuotaService
from storage import org_member_store, org_store, role_store, user_store
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User


@pytest.fixture
async def user_quota_api(async_session_maker, session_maker, create_user, monkeypatch):
    with session_maker() as session:
        admin_role = session.scalar(select(Role).where(Role.name == 'admin'))
        owner_role = session.scalar(select(Role).where(Role.name == 'owner'))
        caller = create_user(role_id=admin_role.id)
        session.add(
            OrgMember(
                org_id=caller.current_org_id,
                user_id=caller.id,
                role_id=owner_role.id,
                _llm_api_key='',
            )
        )
        session.commit()

    monkeypatch.setattr('server.routes.quota.a_session_maker', async_session_maker)
    for module in (org_member_store, org_store, role_store, user_store):
        monkeypatch.setattr(module, 'a_session_maker', async_session_maker)

    auth = SimpleNamespace(api_key_org_id=None)
    auth.get_api_key_org_id = lambda: auth.api_key_org_id
    app = FastAPI()
    app.include_router(quota_admin_router)
    app.dependency_overrides[get_user_id] = lambda: str(caller.id)

    @app.middleware('http')
    async def attach_auth(request, call_next):
        request.state.user_auth = auth
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        yield SimpleNamespace(client=client, caller=caller, auth=auth, app=app)


def _user_url(user_id):
    return f'/api/admin/quota/users/{user_id}/quota'


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [50, -1, None])
async def test_set_user_quota_persists_and_resolves(
    user_quota_api, create_user, create_org, async_session_maker, limit
):
    org = create_org(daily_sandbox_limit=12)
    target = create_user(current_org_id=org.id, daily_sandbox_limit=30)
    response = await user_quota_api.client.put(
        _user_url(target.id), json={'daily_sandbox_limit': limit}
    )
    assert response.status_code == 200
    assert response.json() == {
        'user_id': str(target.id),
        'daily_sandbox_limit': limit,
    }
    async with async_session_maker() as session:
        stored = await session.get(User, target.id)
        assert stored.daily_sandbox_limit == limit
        assert stored.work_email_verified_at is None
        assert await DailySandboxQuotaService(session).get_limit(str(target.id)) == (
            12 if limit is None else None if limit == -1 else limit
        )


@pytest.mark.asyncio
async def test_user_quota_clear_inherits_deployment_default(
    user_quota_api, create_user, async_session_maker, monkeypatch
):
    monkeypatch.setenv('OH_DAILY_SANDBOX_LIMIT', '20')
    target = create_user(daily_sandbox_limit=50)
    response = await user_quota_api.client.put(
        _user_url(target.id), json={'daily_sandbox_limit': None}
    )
    assert response.status_code == 200
    async with async_session_maker() as session:
        assert await DailySandboxQuotaService(session).get_limit(str(target.id)) == 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'daily_sandbox_limit': 0},
        {'daily_sandbox_limit': -2},
        {'daily_sandbox_limit': -11},
        {'daily_sandbox_limit': True},
        {'daily_sandbox_limit': 1.5},
        {'daily_sandbox_limit': 2147483648},
        {},
    ],
)
async def test_invalid_user_quota_leaves_limit_unchanged(
    user_quota_api, create_user, async_session_maker, body
):
    target = create_user(daily_sandbox_limit=30)
    response = await user_quota_api.client.put(_user_url(target.id), json=body)
    assert response.status_code == 422
    async with async_session_maker() as session:
        assert (await session.get(User, target.id)).daily_sandbox_limit == 30


@pytest.mark.asyncio
async def test_user_quota_unknown_user(user_quota_api):
    response = await user_quota_api.client.put(
        _user_url(uuid.uuid4()), json={'daily_sandbox_limit': 50}
    )
    assert response.status_code == 404
    assert response.json()['detail'] == 'User not found'


@pytest.mark.asyncio
async def test_user_quota_malformed_user_id(user_quota_api):
    response = await user_quota_api.client.put(
        _user_url('not-a-uuid'), json={'daily_sandbox_limit': 50}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_org_owner_cannot_set_user_quota(
    user_quota_api, create_user, async_session_maker
):
    target = create_user(daily_sandbox_limit=30)
    async with async_session_maker() as session:
        caller = await session.get(User, user_quota_api.caller.id)
        caller.role_id = None
        await session.commit()
    response = await user_quota_api.client.put(
        _user_url(target.id), json={'daily_sandbox_limit': 50}
    )
    assert response.status_code == 403
    async with async_session_maker() as session:
        assert (await session.get(User, target.id)).daily_sandbox_limit == 30


@pytest.mark.asyncio
async def test_org_bound_key_cannot_set_global_user_quota(
    user_quota_api, create_user, async_session_maker
):
    target = create_user(
        current_org_id=user_quota_api.caller.current_org_id, daily_sandbox_limit=30
    )
    user_quota_api.auth.api_key_org_id = target.current_org_id
    response = await user_quota_api.client.put(
        _user_url(target.id), json={'daily_sandbox_limit': 50}
    )
    assert response.status_code == 403
    async with async_session_maker() as session:
        assert (await session.get(User, target.id)).daily_sandbox_limit == 30


@pytest.mark.asyncio
async def test_unauthenticated_user_quota_update(user_quota_api, create_user):
    target = create_user()
    user_quota_api.app.dependency_overrides[get_user_id] = lambda: None
    response = await user_quota_api.client.put(
        _user_url(target.id), json={'daily_sandbox_limit': 50}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_quota_logs_caller_target_and_limit(
    user_quota_api, create_user, caplog, monkeypatch
):
    target = create_user()
    monkeypatch.setattr(logging.getLogger('openhands'), 'propagate', True)
    with caplog.at_level(logging.INFO, logger='openhands'):
        response = await user_quota_api.client.put(
            _user_url(target.id), json={'daily_sandbox_limit': -1}
        )
    assert response.status_code == 200
    record = next(r for r in caplog.records if r.message == 'user_quota:set')
    assert record.caller_user_id == str(user_quota_api.caller.id)
    assert record.user_id == str(target.id)
    assert record.daily_sandbox_limit == -1
    assert record.exempt is True
