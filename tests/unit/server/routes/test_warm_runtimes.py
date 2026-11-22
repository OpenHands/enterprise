"""Route-level tests for the super-admin warm runtime CRUD API.

Authorization runs through the real ``require_permission``; runtime-api is an
in-process fake (``tests.unit.fake_runtime_api``) reached through the real
``WarmRuntimeAdminService``.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.warm_runtimes import warm_runtime_admin_router
from server.services.warm_runtime_admin_service import WarmRuntimeAdminService
from tests.unit.fake_runtime_api import (
    ADMIN_PASSWORD,
    API_KEY,
    create_fake_runtime_api,
)

BASE = '/api/admin/warm-runtimes'
BODY = {
    'name': 'large',
    'image': 'ghcr.io/openhands/agent-server:1',
    'working_dir': '/workspace',
    'command': ['agent-server'],
    'environment': {'A': 'b'},
    'resources': {
        'requests': {'cpu': '1', 'memory': '2Gi'},
        'limits': {'cpu': '2', 'memory': '4Gi'},
    },
    'idle_seconds': 600,
    'count': 1,
}


@pytest.fixture
def runtime_api(monkeypatch):
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'http://runtime-api')
    monkeypatch.setenv('SANDBOX_API_KEY', API_KEY)
    monkeypatch.setenv('RUNTIME_API_ADMIN_PASSWORD', ADMIN_PASSWORD)
    app = create_fake_runtime_api()
    service = WarmRuntimeAdminService(transport=httpx.ASGITransport(app=app))
    with patch('server.routes.warm_runtimes.warm_runtime_admin_service', service):
        yield app


@pytest.fixture
def client(runtime_api):
    app = FastAPI()
    app.include_router(warm_runtime_admin_router)
    app.dependency_overrides[get_user_id] = lambda: str(uuid.uuid4())
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture
def as_super_admin():
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


@pytest.mark.parametrize(
    'method,path,kwargs',
    [
        ('get', BASE, {}),
        ('post', BASE, {'json': BODY}),
        ('put', f'{BASE}/large', {'json': BODY}),
        ('delete', f'{BASE}/large', {}),
    ],
)
async def test_non_super_admins_are_forbidden(
    client, runtime_api, method, path, kwargs
):
    with patch(
        'server.auth.authorization.get_user_org_role', AsyncMock(return_value=None)
    ):
        resp = await getattr(client, method)(path, **kwargs)

    assert resp.status_code == 403
    assert runtime_api.state.configs == {}
    assert runtime_api.state.logins == 0


async def test_create_then_list_returns_the_saved_config(client, as_super_admin):
    created = await client.post(BASE, json=BODY)
    listed = await client.get(BASE)

    assert created.status_code == 201
    assert created.json()['name'] == 'large'
    assert created.json()['source'] == 'db'
    [config] = listed.json()['configs']
    assert config['resources']['requests']['cpu'] == '1'
    assert config['resources']['limits']['memory'] == '4Gi'
    assert config['idle_seconds'] == 600


async def test_create_duplicate_name_conflicts(client, as_super_admin):
    await client.post(BASE, json=BODY)

    resp = await client.post(BASE, json={**BODY, 'image': 'other'})

    assert resp.status_code == 409
    [config] = (await client.get(BASE)).json()['configs']
    assert config['image'] == BODY['image']


async def test_update_replaces_the_config(client, as_super_admin):
    await client.post(BASE, json=BODY)
    update = {k: v for k, v in BODY.items() if k not in ('name', 'resources')}

    resp = await client.put(f'{BASE}/large', json={**update, 'idle_seconds': 60})

    assert resp.status_code == 200
    assert resp.json()['idle_seconds'] == 60
    assert resp.json()['resources'] is None


async def test_update_unknown_name_is_not_found(client, as_super_admin, runtime_api):
    resp = await client.put(f'{BASE}/ghost', json=BODY)

    assert resp.status_code == 404
    assert runtime_api.state.configs == {}


async def test_delete_removes_the_config(client, as_super_admin):
    await client.post(BASE, json=BODY)

    resp = await client.delete(f'{BASE}/large')

    assert resp.status_code == 204
    assert (await client.get(BASE)).json()['configs'] == []


async def test_delete_unknown_name_is_not_found(client, as_super_admin):
    assert (await client.delete(f'{BASE}/ghost')).status_code == 404


async def test_configmap_configs_cannot_be_deleted(client, as_super_admin, runtime_api):
    runtime_api.state.configs['baked'] = {
        'image': 'i',
        'working_dir': '/w',
        'environment': {},
        'source': 'file',
    }

    resp = await client.delete(f'{BASE}/baked')

    assert resp.status_code == 409
    assert 'baked' in runtime_api.state.configs


@pytest.mark.parametrize(
    'override',
    [
        {'name': 'bad name'},
        {'name': '../x'},
        {'image': ''},
        {'idle_seconds': 0},
        {'count': -1},
        {'resources': {'requests': {'gpu': '1'}}},
    ],
)
async def test_invalid_bodies_are_rejected_before_reaching_runtime_api(
    client, as_super_admin, runtime_api, override
):
    resp = await client.post(BASE, json={**BODY, **override})

    assert resp.status_code == 422
    assert runtime_api.state.configs == {}


async def test_unconfigured_admin_password_is_service_unavailable(
    client, as_super_admin, monkeypatch
):
    monkeypatch.delenv('RUNTIME_API_ADMIN_PASSWORD')

    resp = await client.post(BASE, json=BODY)

    assert resp.status_code == 503
    assert 'RUNTIME_API_ADMIN_PASSWORD' in resp.json()['detail']
