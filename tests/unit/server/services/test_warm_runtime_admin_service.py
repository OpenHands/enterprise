import httpx
import pytest

from server.services.warm_runtime_admin_service import (
    WarmRuntimeAdminError,
    WarmRuntimeAdminService,
)
from tests.unit.fake_runtime_api import API_KEY, create_fake_runtime_api

CONFIG = {'image': 'img:1', 'working_dir': '/w', 'environment': {}}


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'http://runtime-api/')
    monkeypatch.setenv('SANDBOX_API_KEY', API_KEY)
    return WarmRuntimeAdminService(
        transport=httpx.ASGITransport(app=create_fake_runtime_api())
    )


async def test_save_list_and_delete_round_trip(service):
    saved = await service.save_config('big', CONFIG)
    assert saved['source'] == 'db'

    assert [c['name'] for c in await service.list_configs()] == ['big']

    await service.delete_config('big')
    assert await service.list_configs() == []


async def test_rejected_api_key_is_a_bad_gateway(service, monkeypatch):
    monkeypatch.setenv('SANDBOX_API_KEY', 'wrong')

    with pytest.raises(WarmRuntimeAdminError) as exc:
        await service.save_config('a', CONFIG)

    assert exc.value.status_code == 502
    assert 'Invalid API key' in exc.value.detail


@pytest.mark.parametrize(
    'variable', ['SANDBOX_REMOTE_RUNTIME_API_URL', 'SANDBOX_API_KEY']
)
async def test_missing_runtime_api_config_is_service_unavailable(
    service, monkeypatch, variable
):
    monkeypatch.delenv(variable)

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
