from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from server.licensing import service as service_module
from server.licensing.config import LicenseConfig
from server.licensing.service import (
    LicenseService,
    require_feature,
    require_license,
)
from tests.unit.license_signing import (
    LICENSE_KEY,
    NOW,
    Signer,
    invalid_payload,
    offline_payload,
    valid_payload,
)


class FakeCustomerCenter:
    """Answers license checks with whatever the test sets in ``response``."""

    def __init__(self):
        self.response: httpx.Response | Exception = httpx.Response(500)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def _config(**overrides) -> LicenseConfig:
    values = {
        'enforcement_enabled': True,
        'license_key': LICENSE_KEY,
        'license_server_url': 'https://cc.example',
        'check_interval_seconds': 1800,
    }
    values.update(overrides)
    return LicenseConfig(**values)


@pytest.fixture
def customer_center():
    return FakeCustomerCenter()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_service(signer: Signer, customer_center: FakeCustomerCenter, clock: Clock):
    def make(config_loader=None, **overrides) -> LicenseService:
        return LicenseService(
            _config(**overrides),
            config_loader=config_loader,
            http_client_factory=customer_center.client,
            clock=clock,
        )

    return make


async def test_disabled_allows_everything_and_never_calls_out(
    make_service, customer_center
):
    service = make_service(enforcement_enabled=False, license_key=None)

    assert (await service.check_now()).state == 'disabled'
    assert service.is_licensed()
    assert service.has_feature('sso')
    assert service.within_limit('users', 1_000_000)
    assert customer_center.requests == []


async def test_missing_key_is_not_licensed(make_service, customer_center):
    service = make_service(license_key=None)

    assert (await service.check_now()).state == 'missing_key'
    assert not service.is_licensed()
    assert not service.has_feature('sso')
    assert service.within_limit('users', 1)
    assert not service.within_limit('users', 2)
    assert customer_center.requests == []


async def test_pending_allows_until_the_first_check(make_service):
    service = make_service()

    assert service.status().state == 'pending'
    assert service.is_licensed()
    assert service.has_feature('sso')


async def test_valid_answer_sets_features_and_limits(
    make_service, customer_center, signer
):
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))
    service = make_service()

    status = await service.check_now()

    assert status.state == 'valid'
    assert status.last_checked_at == NOW
    assert service.is_licensed()
    assert service.has_feature('sso')
    assert service.within_limit('users', 10)
    assert not service.within_limit('users', 11)
    assert service.within_limit('sandboxes', 1_000_000)  # None is unlimited

    [request] = customer_center.requests
    assert str(request.url) == 'https://cc.example/api/v1/licenses/validate'
    assert json.loads(request.content) == {'license_key': LICENSE_KEY}


async def test_feature_missing_from_plan(make_service, customer_center, signer):
    customer_center.response = httpx.Response(
        200, json=signer.sign(valid_payload(features={'sso': False}))
    )
    service = make_service()
    await service.check_now()

    assert service.is_licensed()
    assert not service.has_feature('sso')


async def test_invalid_answer_is_not_licensed(make_service, customer_center, signer):
    customer_center.response = httpx.Response(
        200, json=signer.sign(invalid_payload('expired'))
    )
    service = make_service()

    status = await service.check_now()

    assert status.state == 'invalid'
    assert status.reason == 'expired'
    assert status.payload is not None
    assert not service.is_licensed()
    assert not service.has_feature('sso')
    assert service.within_limit('users', 1)
    assert not service.within_limit('users', 2)


async def test_invalid_answer_blocks_nothing_while_enforcement_is_off(
    make_service, customer_center, signer
):
    customer_center.response = httpx.Response(
        200, json=signer.sign(invalid_payload('expired'))
    )
    service = make_service(enforcement_enabled=False)

    status = await service.check_now()

    assert status.state == 'invalid'
    assert status.reason == 'expired'
    assert service.is_licensed()
    assert service.has_feature('sso')
    assert service.within_limit('users', 1_000_000)


@pytest.mark.parametrize(
    'response',
    [
        httpx.Response(503),
        httpx.Response(200, text='not json'),
        httpx.ConnectError('down'),
    ],
)
async def test_failed_check_keeps_the_cached_answer_until_it_expires(
    make_service, customer_center, signer, clock, response
):
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))
    service = make_service()
    await service.check_now()

    customer_center.response = response
    clock.now = NOW + timedelta(days=13)
    status = await service.check_now()

    assert status.state == 'valid'
    assert status.last_error is not None

    clock.now = NOW + timedelta(days=14)
    status = service.status()
    assert status.state == 'unverified'
    assert not service.is_licensed()


async def test_forged_answer_does_not_replace_the_cached_one(
    make_service, customer_center, signer
):
    customer_center.response = httpx.Response(
        200, json=signer.sign(invalid_payload('invalid_key'))
    )
    service = make_service()
    await service.check_now()

    forged = Signer(key_id=signer.key_id).sign(valid_payload())
    customer_center.response = httpx.Response(200, json=forged)
    status = await service.check_now()

    assert status.state == 'invalid'
    assert 'signature' in (status.last_error or '')


async def test_first_check_failing_is_unverified(make_service, customer_center):
    customer_center.response = httpx.ConnectError('down')
    service = make_service()

    assert (await service.check_now()).state == 'unverified'
    assert not service.is_licensed()


async def test_new_answer_replaces_the_cached_one(
    make_service, customer_center, signer
):
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))
    service = make_service()
    await service.check_now()

    customer_center.response = httpx.Response(
        200, json=signer.sign(invalid_payload('invalid_key'))
    )

    assert (await service.check_now()).state == 'invalid'


@pytest.mark.parametrize('enforcement_enabled', [True, False])
async def test_background_loop_checks_on_start_and_on_each_interval(
    make_service, customer_center, signer, monkeypatch, enforcement_enabled
):
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            await real_sleep(3600)
        await real_sleep(0)

    monkeypatch.setattr(service_module.asyncio, 'sleep', fake_sleep)
    service = make_service(
        check_interval_seconds=120, enforcement_enabled=enforcement_enabled
    )

    service.start()
    for _ in range(50):
        if len(sleeps) >= 3:
            break
        await real_sleep(0)
    await service.stop()

    assert len(customer_center.requests) == 3
    assert sleeps == [120, 120, 120]
    assert service.status().state == 'valid'


async def test_reload_picks_up_a_saved_license(make_service, customer_center, signer):
    # Arrange
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))

    async def saved_license() -> LicenseConfig:
        return _config()

    service = make_service(config_loader=saved_license, license_key=None)

    # Act
    changed = await service.reload()

    # Assert
    assert changed
    assert service.status().state == 'pending'
    assert (await service.check_now()).state == 'valid'
    assert not await service.reload()


@pytest.mark.parametrize('enforcement_enabled', [True, False])
async def test_background_loop_picks_up_a_license_saved_after_start(
    make_service, customer_center, signer, monkeypatch, enforcement_enabled
):
    # Arrange
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))
    saved = [_config(license_key=None, enforcement_enabled=enforcement_enabled)]
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def saved_license() -> LicenseConfig:
        return saved[0]

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        # A Super Admin saves a license while the loop waits.
        saved[0] = _config(enforcement_enabled=enforcement_enabled)
        if len(sleeps) >= 2:
            await real_sleep(3600)
        await real_sleep(0)

    monkeypatch.setattr(service_module.asyncio, 'sleep', fake_sleep)
    service = make_service(
        config_loader=saved_license,
        license_key=None,
        enforcement_enabled=enforcement_enabled,
    )

    # Act
    service.start()
    for _ in range(50):
        if len(sleeps) >= 2:
            break
        await real_sleep(0)
    await service.stop()

    # Assert
    assert len(customer_center.requests) == 1
    assert service.status().state == 'valid'


async def test_start_does_nothing_when_disabled(make_service):
    service = make_service(enforcement_enabled=False, license_key=None)
    service.start()

    assert service._task is None
    await service.stop()


@pytest.fixture
def installed_service(make_service, monkeypatch):
    service = make_service()
    monkeypatch.setattr(service_module, '_license_service', service)
    return service


async def test_dependencies_reject_when_not_licensed(
    installed_service, customer_center, signer
):
    customer_center.response = httpx.Response(
        200, json=signer.sign(invalid_payload('invalid_key'))
    )
    await installed_service.check_now()

    with pytest.raises(HTTPException) as exc:
        require_license()
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        require_feature('sso')()


async def test_dependencies_allow_on_a_router(
    installed_service, customer_center, signer
):
    customer_center.response = httpx.Response(200, json=signer.sign(valid_payload()))
    await installed_service.check_now()

    from fastapi import Depends

    app = FastAPI()

    @app.get(
        '/sso', dependencies=[Depends(require_license), Depends(require_feature('sso'))]
    )
    async def sso():
        return {'ok': True}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get('/sso')
    assert response.status_code == 200


@pytest.mark.parametrize('value', ['true', '1', 'TRUE'])
def test_config_enables_with_true_or_1(monkeypatch, value):
    monkeypatch.setenv('LICENSE_ENFORCEMENT_ENABLED', value)
    assert LicenseConfig.from_env().enforcement_enabled


def test_config_defaults(monkeypatch):
    for name in (
        'LICENSE_ENFORCEMENT_ENABLED',
        'OPENHANDS_LICENSE_KEY',
        'LICENSE_SERVER_URL',
        'LICENSE_CHECK_INTERVAL_SECONDS',
        'OPENHANDS_OFFLINE_LICENSE',
        'OPENHANDS_OFFLINE_LICENSE_FILE',
    ):
        monkeypatch.delenv(name, raising=False)

    config = LicenseConfig.from_env()
    assert not config.offline

    assert config.enforcement_enabled is False
    assert config.license_key is None
    assert config.license_server_url == 'https://customercenter.openhands.dev'
    assert config.check_interval_seconds == 1800


@pytest.mark.parametrize(('raw', 'expected'), [('600', 600), ('5', 60), ('soon', 1800)])
def test_config_check_interval(monkeypatch, raw, expected):
    monkeypatch.setenv('LICENSE_CHECK_INTERVAL_SECONDS', raw)
    assert LicenseConfig.from_env().check_interval_seconds == expected


def test_config_trims_the_server_url(monkeypatch):
    monkeypatch.setenv('LICENSE_SERVER_URL', 'https://cc.example/ ')
    assert LicenseConfig.from_env().license_server_url == 'https://cc.example'


def test_config_reads_an_offline_license(monkeypatch):
    monkeypatch.setenv('OPENHANDS_OFFLINE_LICENSE', ' {"key_id": "k"} ')
    monkeypatch.setenv('OPENHANDS_OFFLINE_LICENSE_FILE', '/etc/openhands/license.json')

    config = LicenseConfig.from_env()

    assert config.offline
    assert config.offline_license == '{"key_id": "k"}'
    assert config.offline_license_file == '/etc/openhands/license.json'


def test_config_is_online_without_an_offline_license(monkeypatch):
    monkeypatch.setenv('OPENHANDS_OFFLINE_LICENSE', ' ')
    monkeypatch.delenv('OPENHANDS_OFFLINE_LICENSE_FILE', raising=False)

    assert not LicenseConfig.from_env().offline


# Offline license files

LICENSE_ENDS_AT = NOW + timedelta(days=30)


@pytest.fixture
def license_file(tmp_path, signer):
    """Writes a signed payload where OHE reads its offline license."""
    path = tmp_path / 'license.json'

    def write(payload: dict | None = None) -> str:
        body = signer.sign(payload or offline_payload(LICENSE_ENDS_AT))
        path.write_text(json.dumps(body, indent=2))
        return str(path)

    return write


async def test_offline_license_is_licensed_without_calling_customer_center(
    make_service, customer_center, license_file
):
    service = make_service(offline_license_file=license_file())

    status = await service.check_now()

    assert status.state == 'valid'
    assert status.reason is None
    assert service.is_licensed()
    assert service.has_feature('sso')
    assert service.within_limit('users', 10)
    assert not service.within_limit('users', 11)
    assert customer_center.requests == []


@pytest.mark.parametrize('license_key', [None, 'ohe_lic_some_other_key'])
async def test_offline_license_needs_no_license_key(
    make_service, customer_center, license_file, license_key
):
    service = make_service(license_key=license_key, offline_license_file=license_file())

    assert (await service.check_now()).state == 'valid'
    assert service.is_licensed()
    assert customer_center.requests == []


async def test_offline_license_is_read_while_enforcement_is_off(
    make_service, customer_center, license_file
):
    service = make_service(
        enforcement_enabled=False,
        license_key=None,
        offline_license_file=license_file(),
    )

    status = await service.check_now()

    assert status.state == 'valid'
    assert service.within_limit('users', 1_000_000)
    assert customer_center.requests == []


async def test_offline_license_from_its_contents(make_service, customer_center, signer):
    contents = json.dumps(signer.sign(offline_payload()), indent=2)
    service = make_service(offline_license=contents)

    assert (await service.check_now()).state == 'valid'
    assert customer_center.requests == []


async def test_offline_license_ends_at_license_ends_at(
    make_service, license_file, clock
):
    service = make_service(offline_license_file=license_file())
    clock.now = LICENSE_ENDS_AT - timedelta(seconds=1)
    assert (await service.check_now()).state == 'valid'

    clock.now = LICENSE_ENDS_AT
    status = service.status()

    assert status.state == 'invalid'
    assert status.reason == 'expired'
    assert getattr(status.payload, 'license_ends_at') == LICENSE_ENDS_AT
    assert not service.is_licensed()
    assert not service.has_feature('sso')
    assert service.within_limit('users', 1)
    assert not service.within_limit('users', 2)


async def test_offline_license_past_expires_at_still_says_expired(
    make_service, license_file, clock
):
    service = make_service(offline_license_file=license_file())
    clock.now = LICENSE_ENDS_AT + timedelta(days=60)

    status = await service.check_now()

    assert status.state == 'invalid'
    assert status.reason == 'expired'
    assert getattr(status.payload, 'license_ends_at') == LICENSE_ENDS_AT


def _edited(body: dict) -> dict:
    body['payload'] = body['payload'].replace('"paid"', '"trial"')
    return body


@pytest.mark.parametrize(
    'contents',
    [
        pytest.param(
            lambda signer: json.dumps(_edited(signer.sign(offline_payload()))),
            id='edited',
        ),
        pytest.param(
            lambda signer: json.dumps(Signer(signer.key_id).sign(offline_payload())),
            id='forged',
        ),
        pytest.param(
            lambda signer: json.dumps(signer.sign(valid_payload())),
            id='license check answer',
        ),
        pytest.param(lambda signer: 'not json', id='not json'),
    ],
)
async def test_bad_offline_license_is_an_invalid_file(
    make_service, customer_center, signer, contents
):
    service = make_service(offline_license=contents(signer))

    status = await service.check_now()

    assert status.state == 'invalid'
    assert status.reason == 'invalid_license_file'
    assert status.payload is None
    assert status.last_error is not None
    assert not service.is_licensed()
    assert customer_center.requests == []


async def test_missing_offline_license_file_is_an_invalid_file(make_service, tmp_path):
    service = make_service(offline_license_file=str(tmp_path / 'missing.json'))

    status = await service.check_now()

    assert status.reason == 'invalid_license_file'
    assert 'FileNotFoundError' in (status.last_error or '')


async def test_both_offline_settings_is_an_invalid_file(
    make_service, signer, license_file
):
    service = make_service(
        offline_license=json.dumps(signer.sign(offline_payload())),
        offline_license_file=license_file(),
    )

    status = await service.check_now()

    assert status.reason == 'invalid_license_file'
    assert 'not both' in (status.last_error or '')


async def test_replaced_offline_license_file_takes_effect_on_the_next_check(
    make_service, license_file, clock
):
    path = license_file()
    service = make_service(offline_license_file=path)
    await service.check_now()
    clock.now = LICENSE_ENDS_AT
    assert service.status().reason == 'expired'

    license_file(offline_payload(LICENSE_ENDS_AT + timedelta(days=365)))
    assert (await service.check_now()).state == 'valid'

    Path(path).write_text('{}')
    assert (await service.check_now()).reason == 'invalid_license_file'


async def test_offline_license_is_checked_every_hour(
    make_service, customer_center, license_file, monkeypatch
):
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            await real_sleep(3600)
        await real_sleep(0)

    monkeypatch.setattr(service_module.asyncio, 'sleep', fake_sleep)
    service = make_service(
        license_key=None,
        offline_license_file=license_file(),
        check_interval_seconds=120,
    )

    service.start()
    for _ in range(50):
        if len(sleeps) >= 2:
            break
        await real_sleep(0)
    await service.stop()

    assert sleeps == [3600, 3600]
    assert service.status().state == 'valid'
    assert customer_center.requests == []
