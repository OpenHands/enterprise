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
from server.routes.service_accounts import (
    service_accounts_router,
    validate_service_accounts_enabled,
)
from server.services.admin_user_lifecycle_service import LifecycleCleanupError
from server.services.feature_flag_service import FeatureFlagService
from server.services.service_account_service import ServiceAccountNameExistsError
from storage.feature_flag_store import FeatureFlagStore
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


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(service_accounts_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_ID
    return app


@pytest.fixture
def app():
    """The router with the ENABLE_SERVICE_ACCOUNTS gate open.

    The gate itself is covered by the ``Feature flag`` tests below.
    """
    app = _build_app()
    app.dependency_overrides[validate_service_accounts_enabled] = lambda: None
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
    svc.get_service_account.side_effect = [_sa(is_disabled=True), _sa()]
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


def _enable_store():
    store = MagicMock()
    store.rotate_managed_llm_key = AsyncMock(
        return_value=ManagedLlmKeyRotation(status=ManagedLlmKeyStatus.ROTATED)
    )
    store.resolve_valid_managed_llm_key = AsyncMock(return_value='sk-valid')
    return store


@pytest.mark.asyncio
async def test_enable_of_active_account_does_not_force_rotate(
    client, as_admin, svc, lifecycle
):
    """An enabled account keeps its valid key: in-flight sandboxes hold it."""
    store = _enable_store()
    with (
        patch(
            f'{MODULE}.SaasSettingsStore.get_instance', AsyncMock(return_value=store)
        ),
        patch(f'{MODULE}.is_litellm_enabled', AsyncMock(return_value=True)),
    ):
        async with client as c:
            resp = await c.post(_url('/enable'))

    assert resp.status_code == 200
    store.rotate_managed_llm_key.assert_not_awaited()
    store.resolve_valid_managed_llm_key.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_enable_of_disabled_account_rotates(client, as_admin, svc, lifecycle):
    svc.get_service_account.side_effect = [_sa(is_disabled=True), _sa()]
    store = _enable_store()
    with (
        patch(
            f'{MODULE}.SaasSettingsStore.get_instance', AsyncMock(return_value=store)
        ),
        patch(f'{MODULE}.is_litellm_enabled', AsyncMock(return_value=True)),
    ):
        async with client as c:
            resp = await c.post(_url('/enable'))

    assert resp.status_code == 200
    store.rotate_managed_llm_key.assert_awaited_once_with()
    store.resolve_valid_managed_llm_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_enable_retry_after_failed_rotation_restores_the_key(
    client, as_admin, svc, lifecycle
):
    """The account is already enabled but its key is gone: the retry mints it."""
    store = _enable_store()
    store.resolve_valid_managed_llm_key = AsyncMock(
        side_effect=[RuntimeError('litellm down'), 'sk-new']
    )
    with (
        patch(
            f'{MODULE}.SaasSettingsStore.get_instance', AsyncMock(return_value=store)
        ),
        patch(f'{MODULE}.is_litellm_enabled', AsyncMock(return_value=True)),
    ):
        async with client as c:
            first = await c.post(_url('/enable'))
            retry = await c.post(_url('/enable'))

    assert first.status_code == 503
    assert retry.status_code == 200
    assert store.resolve_valid_managed_llm_key.await_count == 2


@pytest.mark.asyncio
async def test_delete_forwards_every_credential_header(
    client, as_admin, svc, lifecycle
):
    get = AsyncMock(return_value=_automation_response([], total=0))
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(
                _url(),
                headers={
                    'X-Session-API-Key': 'sess',
                    'X-Access-Token': 'tok',
                    'Cookie': 'api_key=c',
                },
            )

    assert resp.status_code == 204
    forwarded = get.await_args_list[0].kwargs['headers']
    assert forwarded['x-session-api-key'] == 'sess'
    assert forwarded['x-access-token'] == 'tok'
    assert forwarded['cookie'] == 'api_key=c'


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
    """The check filters by owner in one request and does not page."""
    get = AsyncMock(
        return_value=_automation_response([{'user_id': str(SA_ID)}], total=1)
    )
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(
                _url(), headers={'Authorization': 'Bearer k', 'Cookie': 'a=b'}
            )

    assert resp.status_code == 409
    lifecycle.delete_service_account.assert_not_awaited()
    get.assert_awaited_once()
    call = get.await_args_list[0]
    assert call.args[0] == 'http://automation/v1'
    assert call.kwargs['params']['run_as_user_id'] == str(SA_ID)
    assert call.kwargs['headers']['authorization'] == 'Bearer k'
    assert call.kwargs['headers']['X-Org-Id'] == str(ORG_ID)


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


def _real_response(status_code: int, **kwargs) -> httpx.Response:
    """A real response, so ``raise_for_status`` and ``json`` behave as in prod."""
    request = httpx.Request('GET', 'http://automation/v1')
    return httpx.Response(status_code, request=request, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'response',
    [
        pytest.param(_real_response(500, json={'total': 0}), id='5xx-status'),
        pytest.param(_real_response(200, json={'automations': []}), id='no-total'),
        pytest.param(_real_response(200, json={'total': None}), id='null-total'),
        pytest.param(_real_response(200, content=b'<html>not json'), id='non-json'),
    ],
)
async def test_delete_fails_closed_on_a_bad_automation_service_answer(
    client, as_admin, svc, lifecycle, response
):
    get = AsyncMock(return_value=response)
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 503
    lifecycle.delete_service_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_sandbox_cleanup_failure_returns_503(
    client, as_admin, svc, lifecycle
):
    lifecycle.delete_service_account.side_effect = LifecycleCleanupError('retry')

    with patch(f'{MODULE}.AUTOMATION_SERVICE_URL', ''):
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 503
    lifecycle.delete_service_account.assert_awaited_once_with(str(SA_ID))


@pytest.mark.asyncio
async def test_delete_succeeds_when_no_automation_is_owned(
    client, as_admin, svc, lifecycle
):
    get = AsyncMock(return_value=_automation_response([], total=0))
    url_patch, client_patch = _patch_automation_service(get)

    with url_patch, client_patch:
        async with client as c:
            resp = await c.delete(_url())

    assert resp.status_code == 204
    assert get.await_args_list[0].kwargs['params']['run_as_user_id'] == str(SA_ID)
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
    """E.g. the service account is the last owner of a team organization."""
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


# --- Secrets ---


@pytest.fixture
def secrets_store():
    store = MagicMock()
    store.list_personal = AsyncMock(return_value=[('A', 'first'), ('B', None)])
    store.upsert_personal = AsyncMock()
    store.delete_personal = AsyncMock(return_value=True)
    with patch(
        f'{MODULE}.SaasSecretsStore.get_instance', AsyncMock(return_value=store)
    ) as get_instance:
        store.get_instance = get_instance
        yield store


def _secret_url(name: str = '') -> str:
    return _url('/secrets' + (f'/{name}' if name else ''))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'name', 'body'),
    [('GET', '', None), ('PUT', 'A', {'value': 'v'}), ('DELETE', 'A', None)],
)
async def test_secret_routes_require_manage_service_accounts(
    client, svc, secrets_store, method, name, body
):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=_role('member')),
    ):
        async with client as c:
            resp = await c.request(method, _secret_url(name), json=body)

    assert resp.status_code == 403
    secrets_store.upsert_personal.assert_not_awaited()
    secrets_store.delete_personal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'name', 'body'),
    [('GET', '', None), ('PUT', 'A', {'value': 'v'}), ('DELETE', 'A', None)],
)
async def test_secret_routes_reject_human_target(
    client, as_admin, svc, secrets_store, method, name, body
):
    svc.get_service_account = AsyncMock(return_value=None)

    async with client as c:
        resp = await c.request(method, _secret_url(name), json=body)

    assert resp.status_code == 404
    secrets_store.get_instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_secrets_returns_names_and_descriptions_only(
    client, as_admin, svc, secrets_store
):
    async with client as c:
        resp = await c.get(_secret_url())

    assert resp.status_code == 200
    assert [(i['name'], i['description']) for i in resp.json()['items']] == [
        ('A', 'first'),
        ('B', None),
    ]
    assert 'value' not in resp.text
    secrets_store.get_instance.assert_awaited_once_with(
        str(SA_ID), effective_org_id=ORG_ID
    )


@pytest.mark.asyncio
async def test_list_secrets_with_a_duplicate_name_does_not_fail(
    client, as_admin, svc, secrets_store
):
    """Names have no unique constraint; sorting must not compare descriptions,
    one of which can be None."""
    secrets_store.list_personal.return_value = [('A', 'second'), ('A', None)]

    async with client as c:
        resp = await c.get(_secret_url())

    assert resp.status_code == 200
    assert [i['name'] for i in resp.json()['items']] == ['A', 'A']


@pytest.mark.asyncio
async def test_put_secret_never_echoes_value(client, as_admin, svc, secrets_store):
    async with client as c:
        resp = await c.put(
            _secret_url('API_TOKEN'),
            json={'value': 'hunter2-secret', 'description': 'token'},
        )

    assert resp.status_code == 200
    assert 'hunter2-secret' not in resp.text
    secrets_store.upsert_personal.assert_awaited_once_with(
        'API_TOKEN', 'hunter2-secret', 'token'
    )


@pytest.mark.asyncio
async def test_put_secret_holds_the_secrets_write_lock(
    client, as_admin, svc, secrets_store
):
    """The write shares the lock key of ``/api/v1/secrets`` for the same user and org."""
    events: list[str] = []

    class _Lock:
        async def __aenter__(self):
            events.append('lock')

        async def __aexit__(self, *exc):
            events.append('unlock')

    secrets_store.upsert_personal.side_effect = lambda *a: events.append('write')
    with patch(f'{MODULE}._secrets_write_lock', return_value=_Lock()) as lock:
        async with client as c:
            await c.put(_secret_url('A'), json={'value': 'v'})

    lock.assert_called_once_with(str(SA_ID), secrets_store)
    assert events == ['lock', 'write', 'unlock']


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['OPENHANDS_API_KEY', '1BAD', 'bad-name'])
async def test_put_invalid_or_reserved_name_returns_400(
    client, as_admin, svc, secrets_store, name
):
    async with client as c:
        resp = await c.put(_secret_url(name), json={'value': 'v'})

    assert resp.status_code == 400
    secrets_store.upsert_personal.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_secret(client, as_admin, svc, secrets_store):
    async with client as c:
        resp = await c.delete(_secret_url('A'))

    assert resp.status_code == 200
    secrets_store.delete_personal.assert_awaited_once_with('A')


@pytest.mark.asyncio
async def test_delete_missing_secret_returns_404(client, as_admin, svc, secrets_store):
    secrets_store.delete_personal.return_value = False

    async with client as c:
        resp = await c.delete(_secret_url('A'))

    assert resp.status_code == 404


# --- Feature flag ---

FLAG = 'ENABLE_SERVICE_ACCOUNTS'

GATED_ROUTES = [
    (m, _url(sfx, SA_ID if has_id else None), b) for m, has_id, sfx, b in ALL_ROUTES
] + [
    ('GET', _secret_url(), None),
    ('PUT', _secret_url('A'), {'value': 'v'}),
    ('DELETE', _secret_url('A'), None),
]


@pytest.fixture
def flag_db(async_session_maker, monkeypatch):
    """Resolve the flag through the real service against the test database."""
    monkeypatch.delenv(FLAG, raising=False)
    FeatureFlagService._cache.clear()
    FeatureFlagService._global_cache.clear()
    with patch('storage.feature_flag_store.a_session_maker', async_session_maker):
        yield
    FeatureFlagService._cache.clear()
    FeatureFlagService._global_cache.clear()


@pytest.fixture
def gated_client():
    return AsyncClient(
        transport=ASGITransport(app=_build_app()), base_url='http://test'
    )


def test_every_route_is_gated():
    assert len(GATED_ROUTES) == len(service_accounts_router.routes)


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), GATED_ROUTES)
async def test_routes_return_403_when_flag_is_off(
    gated_client, flag_db, as_admin, svc, lifecycle, secrets_store, method, url, body
):
    async with gated_client as c:
        resp = await c.request(method, url, json=body)

    assert resp.status_code == 403
    assert FLAG in resp.json()['detail']
    svc.get_service_account.assert_not_awaited()
    svc.list_service_accounts.assert_not_awaited()
    svc.create_service_account.assert_not_awaited()
    secrets_store.get_instance.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('env_value', ['true', '1'])
async def test_routes_work_when_env_var_is_on(
    gated_client, flag_db, as_admin, svc, monkeypatch, env_value
):
    monkeypatch.setenv(FLAG, env_value)

    async with gated_client as c:
        resp = await c.get(_url(sa_id=None))

    assert resp.status_code == 200
    assert resp.json()['items'][0]['id'] == str(SA_ID)


@pytest.mark.asyncio
async def test_routes_work_when_database_row_is_on(
    gated_client, flag_db, as_admin, svc, monkeypatch
):
    monkeypatch.setenv(FLAG, 'false')
    await FeatureFlagStore.create_flag(key=FLAG, enabled=True)

    async with gated_client as c:
        resp = await c.get(_url(sa_id=None))

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_database_row_wins_over_env_var(
    gated_client, flag_db, as_admin, svc, monkeypatch
):
    monkeypatch.setenv(FLAG, 'true')
    await FeatureFlagStore.create_flag(key=FLAG, enabled=False)

    async with gated_client as c:
        resp = await c.get(_url(sa_id=None))

    assert resp.status_code == 403
    svc.list_service_accounts.assert_not_awaited()
