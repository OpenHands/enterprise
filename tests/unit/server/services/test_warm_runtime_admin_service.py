import httpx
import pytest

from server.services.warm_runtime_admin_service import (
    WarmRuntimeAdminError,
    WarmRuntimeAdminService,
)
from tests.unit.fake_runtime_api import (
    ADMIN_PASSWORD,
    API_KEY,
    create_fake_runtime_api,
)

CONFIG = {'image': 'img:1', 'working_dir': '/w', 'environment': {}}


@pytest.fixture
def runtime_api(monkeypatch):
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'http://runtime-api/')
    monkeypatch.setenv('SANDBOX_API_KEY', API_KEY)
    monkeypatch.setenv('RUNTIME_API_ADMIN_PASSWORD', ADMIN_PASSWORD)
    return create_fake_runtime_api()


@pytest.fixture
def service(runtime_api):
    return WarmRuntimeAdminService(transport=httpx.ASGITransport(app=runtime_api))


async def test_save_list_and_delete_round_trip(service):
    saved = await service.save_config('big', CONFIG)
    assert saved['source'] == 'db'

    assert [c['name'] for c in await service.list_configs()] == ['big']

    await service.delete_config('big')
    assert await service.list_configs() == []


async def test_admin_token_is_reused_across_writes(service, runtime_api):
    await service.save_config('a', CONFIG)
    await service.save_config('b', CONFIG)

    assert runtime_api.state.logins == 1


async def test_rejected_token_triggers_one_fresh_login(service, runtime_api):
    await service.save_config('a', CONFIG)
    runtime_api.state.tokens.clear()

    await service.save_config('b', CONFIG)

    assert runtime_api.state.logins == 2


async def test_wrong_admin_password_is_a_bad_gateway(service, monkeypatch):
    monkeypatch.setenv('RUNTIME_API_ADMIN_PASSWORD', 'wrong')

    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.save_config('a', CONFIG)

    assert exc.value.status_code == 502
    assert 'Invalid admin password' in exc.value.detail


async def test_missing_admin_password_disables_writes_but_not_reads(
    service, monkeypatch
):
    monkeypatch.delenv('RUNTIME_API_ADMIN_PASSWORD')

    assert await service.list_configs() == []
    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.save_config('a', CONFIG)

    assert exc.value.status_code == 503
    assert 'RUNTIME_API_ADMIN_PASSWORD' in exc.value.detail


async def test_missing_runtime_api_url_is_service_unavailable(service, monkeypatch):
    monkeypatch.delenv('SANDBOX_REMOTE_RUNTIME_API_URL')

    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.list_configs()

    assert exc.value.status_code == 503


async def test_validation_errors_from_runtime_api_are_passed_through(service):
    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.save_config('a', {**CONFIG, 'idle_seconds': 0})

    assert exc.value.status_code == 422
    assert exc.value.detail == 'idle_seconds: must be greater than 0'


async def test_deleting_unknown_config_is_not_found(service):
    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.delete_config('nope')

    assert exc.value.status_code == 404


async def test_unreachable_runtime_api_is_a_bad_gateway(monkeypatch):
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'http://runtime-api')
    monkeypatch.setenv('SANDBOX_API_KEY', API_KEY)

    def refuse(request):
        raise httpx.ConnectError('refused')

    service = WarmRuntimeAdminService(transport=httpx.MockTransport(refuse))

    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.list_configs()

    assert exc.value.status_code == 502
