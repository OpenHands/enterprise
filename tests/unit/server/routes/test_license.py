from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.licensing import service as service_module
from server.licensing.config import LicenseConfig
from server.licensing.service import LicenseService
from server.routes.license import license_router
from tests.unit.license_signing import (
    LICENSE_KEY,
    NOW,
    Signer,
    invalid_payload,
    offline_payload,
    valid_payload,
)


def _service(signer: Signer, answer: dict | None, **config) -> LicenseService:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=signer.sign(answer or {}))

    values = {
        'enforcement_enabled': True,
        'license_key': LICENSE_KEY,
        'license_server_url': 'https://cc.example',
        'check_interval_seconds': 1800,
    }
    values.update(config)
    return LicenseService(
        LicenseConfig(**values),
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
        clock=lambda: NOW,
    )


async def _get_status(monkeypatch, service: LicenseService) -> dict:
    monkeypatch.setattr(service_module, '_license_service', service)
    app = FastAPI()
    app.include_router(license_router)
    app.dependency_overrides[get_user_id] = lambda: 'user-1'
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get('/api/license/status')
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def signer(monkeypatch) -> Signer:
    signer = Signer()
    signer.trust(monkeypatch)
    return signer


async def test_status_when_disabled(monkeypatch, signer):
    body = await _get_status(
        monkeypatch, _service(signer, None, enforcement_enabled=False)
    )

    assert body['state'] == 'disabled'
    assert body['offline'] is False
    assert body['customer_center_url'] == 'https://cc.example'


async def test_status_for_a_trial(monkeypatch, signer):
    service = _service(signer, valid_payload())
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'valid'
    assert body['offline'] is False
    assert body['type'] == 'trial'
    assert body['license_ends_at'].startswith('2026-11-11T12:00:00')
    assert body['expires_at'].startswith('2026-11-15T12:00:00')
    assert body['reason'] is None


async def test_status_for_a_license_that_never_expires(monkeypatch, signer):
    service = _service(signer, valid_payload(type='paid', license_ends_at=None))
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'valid'
    assert body['type'] == 'paid'
    assert body['license_ends_at'] is None


async def test_status_for_an_expired_license(monkeypatch, signer):
    service = _service(signer, invalid_payload('expired'))
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'invalid'
    assert body['reason'] == 'expired'
    assert body['type'] is None
    assert body['license_ends_at'] is None


async def test_status_for_an_offline_license(monkeypatch, signer):
    service = _service(
        signer, None, offline_license=json.dumps(signer.sign(offline_payload()))
    )
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'valid'
    assert body['offline'] is True
    assert body['type'] == 'paid'
    assert body['license_ends_at'].startswith('2026-12-01T12:00:00')


async def test_status_for_an_ended_offline_license(monkeypatch, signer):
    payload = offline_payload(license_ends_at=NOW - timedelta(days=1))
    service = _service(signer, None, offline_license=json.dumps(signer.sign(payload)))
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'invalid'
    assert body['reason'] == 'expired'
    assert body['offline'] is True
    assert body['license_ends_at'].startswith('2026-10-31T12:00:00')


async def test_status_for_an_invalid_license_file(monkeypatch, signer):
    service = _service(signer, None, offline_license='not json')
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'invalid'
    assert body['reason'] == 'invalid_license_file'
    assert body['offline'] is True
    assert body['license_ends_at'] is None


async def _get_usage(monkeypatch, service: LicenseService, session_maker) -> dict:
    monkeypatch.setattr(service_module, '_license_service', service)
    monkeypatch.setattr('server.routes.license.a_session_maker', session_maker)
    app = FastAPI()
    app.include_router(license_router)
    app.dependency_overrides[get_user_id] = lambda: 'user-1'
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get('/api/license/usage')
    assert response.status_code == 200
    return response.json()


async def test_usage_counts_users_and_orgs_against_the_license(
    monkeypatch, signer, async_session_maker, create_user, create_org
):
    service = _service(signer, valid_payload(limits={'users': 10, 'orgs': 1}))
    await service.check_now()
    for disabled in (False, True):
        # Each user gets a personal workspace, which doesn't count as an org.
        personal = create_org()
        create_user(id=personal.id, current_org_id=personal.id, is_disabled=disabled)
    create_org()

    body = await _get_usage(monkeypatch, service, async_session_maker)

    assert body['users'] == {'used': 1, 'limit': 10}
    assert body['orgs'] == {'used': 1, 'limit': 1}
    assert body['customer_center_url'] == 'https://cc.example'


async def test_usage_has_no_limit_while_enforcement_is_off(
    monkeypatch, signer, async_session_maker, create_user
):
    service = _service(signer, None, enforcement_enabled=False)
    create_user()

    body = await _get_usage(monkeypatch, service, async_session_maker)

    assert body['users'] == {'used': 1, 'limit': None}
    assert body['orgs']['limit'] is None


async def test_usage_requires_a_user(monkeypatch, signer):
    monkeypatch.setattr(
        service_module, '_license_service', _service(signer, None, license_key=None)
    )
    app = FastAPI()
    app.include_router(license_router)
    app.dependency_overrides[get_user_id] = lambda: None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get('/api/license/usage')
    assert response.status_code == 401


async def test_status_reports_the_sso_feature(monkeypatch, signer):
    service = _service(signer, valid_payload(features={'sso': False}))
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['features'] == {'sso': False}


async def test_status_allows_every_feature_while_enforcement_is_off(
    monkeypatch, signer
):
    body = await _get_status(
        monkeypatch, _service(signer, None, enforcement_enabled=False)
    )

    assert body['features'] == {'sso': True}
