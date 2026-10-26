"""Route tests for the service account admin API.

Permission checks run through the real ``require_permission``; the service
layer and the Automation Service are stubbed. Scoping of lookups to the org and
the service account type is covered with a real database in
``tests/unit/server/services/test_service_account_service.py``.
"""

import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.org_models import ServiceAccountResponse
from server.routes.service_accounts import service_accounts_router
from server.services.admin_user_lifecycle_service import LifecycleCleanupError
from server.services.service_account_service import ServiceAccountNameExistsError
from storage.saas_settings_store import ManagedLlmKeyRotation, ManagedLlmKeyStatus
from storage.user_data_cleanup import UserCleanupConflict

CALLER_ID = str(uuid.uuid4())
ORG_ID = uuid.uuid4()
SA_ID = uuid.uuid4()
MODULE = 'server.routes.service_accounts'


def _sa(name: str = 'CI bot', is_disabled: bool = False) -> ServiceAccountResponse:
    return ServiceAccountResponse(
        id=str(SA_ID),
        org_id=str(ORG_ID),
        name=name,
        is_disabled=is_disabled,
        role='service_account',
    )


def _role(name: str):
    role = MagicMock()
    role.name = name
    return role


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(service_accounts_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_ID
    return app


@pytest.fixture
def client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture
def as_admin():
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=_role('admin')),
    ):
        yield


@pytest.fixture
def svc():
    """The service layer, with the service account present in the org."""
    with ExitStack() as stack:
        service = stack.enter_context(patch(f'{MODULE}.ServiceAccountService'))
        service.get_service_account = AsyncMock(return_value=_sa())
        service.list_service_accounts = AsyncMock(return_value=[_sa()])
        service.create_service_account = AsyncMock(return_value=MagicMock(id=SA_ID))
        service.rename_service_account = AsyncMock(return_value=_sa('renamed'))
        yield service


@pytest.fixture
def lifecycle():
    with patch(f'{MODULE}.AdminUserLifecycleService') as cls:
        instance = cls.return_value
        instance.disable_user = AsyncMock()
        instance.enable_user = AsyncMock()
        instance.delete_service_account = AsyncMock(return_value=MagicMock())
        yield instance


def _url(suffix: str = '', sa_id=SA_ID) -> str:
    base = f'/api/organizations/{ORG_ID}/service-accounts'
    return f'{base}/{sa_id}{suffix}' if sa_id else base


# (method, path suffix with sa id?, json)
ALL_ROUTES = [
    ('POST', False, '', {'name': 'x'}),
    ('GET', False, '', None),
    ('GET', True, '', None),
    ('PATCH', True, '', {'name': 'x'}),
    ('DELETE', True, '', None),
    ('POST', True, '/disable', None),
    ('POST', True, '/enable', None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'has_id', 'suffix', 'body'), ALL_ROUTES)
@pytest.mark.parametrize('role_name', ['member', 'service_account'])
async def test_requires_manage_service_accounts(
    client, svc, lifecycle, method, has_id, suffix, body, role_name
):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=_role(role_name)),
    ):
        async with client as c:
            resp = await c.request(
                method, _url(suffix, SA_ID if has_id else None), json=body
            )

    assert resp.status_code == 403
    assert 'manage_service_accounts' in resp.json()['detail']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'suffix', 'body'),
    [
        ('GET', '', None),
        ('PATCH', '', {'name': 'x'}),
        ('DELETE', '', None),
        ('POST', '/disable', None),
        ('POST', '/enable', None),
    ],
)
async def test_sa_routes_return_404_for_human_or_foreign_target(
    client, as_admin, svc, lifecycle, method, suffix, body
):
    """A human, or a service account of another org, is "not found"."""
    svc.get_service_account = AsyncMock(return_value=None)
    svc.rename_service_account = AsyncMock(return_value=None)

    async with client as c:
        resp = await c.request(method, _url(suffix), json=body)

    assert resp.status_code == 404
    lifecycle.disable_user.assert_not_awaited()
    lifecycle.enable_user.assert_not_awaited()
    lifecycle.delete_service_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_returns_service_account(client, as_admin, svc):
    async with client as c:
        resp = await c.post(_url(sa_id=None), json={'name': 'CI bot'})

    assert resp.status_code == 201
    assert resp.json() == _sa().model_dump()
    svc.create_service_account.assert_awaited_once_with(ORG_ID, 'CI bot')


@pytest.mark.asyncio
async def test_create_duplicate_name_returns_409(client, as_admin, svc):
    svc.create_service_account = AsyncMock(
        side_effect=ServiceAccountNameExistsError('CI bot')
    )

    async with client as c:
        resp = await c.post(_url(sa_id=None), json={'name': 'CI bot'})

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_pages_with_next_page_id(client, as_admin, svc):
    svc.list_service_accounts = AsyncMock(return_value=[_sa('a'), _sa('b'), _sa('c')])

    async with client as c:
        resp = await c.get(_url(sa_id=None), params={'limit': 2})

    body = resp.json()
    assert [i['name'] for i in body['items']] == ['a', 'b']
    assert body['next_page_id'] == '2'
    svc.list_service_accounts.assert_awaited_once_with(ORG_ID, offset=0, limit=3)


@pytest.mark.asyncio
async def test_patch_renames(client, as_admin, svc):
    async with client as c:
        resp = await c.patch(_url(), json={'name': 'renamed'})

    assert resp.status_code == 200
    assert resp.json()['name'] == 'renamed'


@pytest.mark.asyncio
async def test_disable_revokes_through_lifecycle_service(
    client, as_admin, svc, lifecycle
):
    svc.get_service_account = AsyncMock(side_effect=[_sa(), _sa(is_disabled=True)])

    async with client as c:
        resp = await c.post(_url('/disable'))

    assert resp.status_code == 200
    assert resp.json()['is_disabled'] is True
    lifecycle.disable_user.assert_awaited_once_with(str(SA_ID))


@pytest.mark.asyncio
async def test_disable_cleanup_failure_returns_503(client, as_admin, svc, lifecycle):
    lifecycle.disable_user.side_effect = LifecycleCleanupError('retry')

    async with client as c:
        resp = await c.post(_url('/disable'))

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_enable_rotates_managed_key(client, as_admin, svc, lifecycle):
    store = MagicMock()
    store.rotate_managed_llm_key = AsyncMock(
        return_value=ManagedLlmKeyRotation(status=ManagedLlmKeyStatus.ROTATED)
    )
    with (
        patch(
            f'{MODULE}.SaasSettingsStore.get_instance', AsyncMock(return_value=store)
        ) as get_instance,
        patch(f'{MODULE}.is_litellm_enabled', AsyncMock(return_value=True)),
    ):
        async with client as c:
            resp = await c.post(_url('/enable'))

    assert resp.status_code == 200
    lifecycle.enable_user.assert_awaited_once_with(str(SA_ID))
    get_instance.assert_awaited_once_with(str(SA_ID), effective_org_id=ORG_ID)
    store.rotate_managed_llm_key.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_enable_rotation_failure_returns_503(client, as_admin, svc, lifecycle):
    with (
        patch(
            f'{MODULE}.SaasSettingsStore.get_instance',
            AsyncMock(side_effect=RuntimeError('litellm down')),
        ),
        patch(f'{MODULE}.is_litellm_enabled', AsyncMock(return_value=True)),
    ):
        async with client as c:
            resp = await c.post(_url('/enable'))

    assert resp.status_code == 503


def _automation_response(automations: list[dict], total: int) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={'automations': automations, 'total': total})
    return resp


def _patch_automation_service(get: AsyncMock, url: str = 'http://automation'):
    client = MagicMock()
    client.get = get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return (
        patch(f'{MODULE}.AUTOMATION_SERVICE_URL', url),
        patch(f'{MODULE}.httpx.AsyncClient', return_value=client),
    )


@pytest.mark.asyncio
async def test_delete_blocked_by_owned_automation(client, as_admin, svc, lifecycle):
    """The owner is found on the second page; other users' automations are ignored."""
    page1 = _automation_response(
        [{'user_id': str(uuid.uuid4())} for _ in range(100)], total=101
    )
    page2 = _automation_response([{'user_id': str(SA_ID)}], total=101)
    get = AsyncMock(side_effect=[page1, page2])
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(
                _url(), headers={'Authorization': 'Bearer k', 'Cookie': 'a=b'}
            )

    assert resp.status_code == 409
    lifecycle.delete_service_account.assert_not_awaited()
    first_call = get.await_args_list[0]
    assert first_call.args[0] == 'http://automation/v1'
    assert first_call.kwargs['headers']['authorization'] == 'Bearer k'
    assert first_call.kwargs['headers']['X-Org-Id'] == str(ORG_ID)
    assert get.await_args_list[1].kwargs['params']['offset'] == 100


@pytest.mark.asyncio
async def test_delete_fails_closed_when_automation_service_unreachable(
    client, as_admin, svc, lifecycle
):
    get = AsyncMock(side_effect=httpx.ConnectError('down'))
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 503
    lifecycle.delete_service_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_succeeds_when_no_automation_is_owned(
    client, as_admin, svc, lifecycle
):
    get = AsyncMock(
        return_value=_automation_response([{'user_id': str(uuid.uuid4())}], total=1)
    )
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 204
    lifecycle.delete_service_account.assert_awaited_once_with(str(SA_ID))


@pytest.mark.asyncio
async def test_delete_without_automation_service_url_skips_the_check(
    client, as_admin, svc, lifecycle
):
    """No Automation Service is configured, so no automation can exist."""
    with patch(f'{MODULE}.AUTOMATION_SERVICE_URL', ''):
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 204
    lifecycle.delete_service_account.assert_awaited_once_with(str(SA_ID))


@pytest.mark.asyncio
async def test_delete_conflict_from_cleanup_returns_409(
    client, as_admin, svc, lifecycle
):
    """E.g. the service account still has sandboxes."""
    lifecycle.delete_service_account.side_effect = UserCleanupConflict('sandboxes')

    with patch(f'{MODULE}.AUTOMATION_SERVICE_URL', ''):
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_operations_are_logged(client, as_admin, svc, lifecycle):
    with patch(f'{MODULE}.logger') as log:
        async with client as c:
            await c.post(_url('/disable'))

    log.info.assert_called_once()
    assert log.info.call_args.args[0] == 'org:service_accounts:disable'
    assert log.info.call_args.kwargs['extra'] == {
        'caller_user_id': CALLER_ID,
        'org_id': str(ORG_ID),
        'service_account_id': str(SA_ID),
    }
