from __future__ import annotations

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
    assert body['customer_center_url'] == 'https://cc.example'


async def test_status_for_a_trial(monkeypatch, signer):
    service = _service(signer, valid_payload())
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'valid'
    assert body['plan'] == 'teams'
    assert body['subscription_state'] == 'trial_active'
    assert body['trial_ends_at'].startswith('2026-11-11T12:00:00')
    assert body['expires_at'].startswith('2026-11-15T12:00:00')
    assert body['reason'] is None


async def test_status_for_an_overdue_subscription(monkeypatch, signer):
    service = _service(
        signer,
        valid_payload(
            plan='enterprise',
            status={
                'state': 'subscription_active_overdue',
                'overdue_since': '2026-10-20T00:00:00.000Z',
            },
        ),
    )
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['subscription_state'] == 'subscription_active_overdue'
    assert body['overdue_since'].startswith('2026-10-20T00:00:00')


async def test_status_for_a_revoked_key(monkeypatch, signer):
    service = _service(signer, invalid_payload('revoked'))
    await service.check_now()

    body = await _get_status(monkeypatch, service)

    assert body['state'] == 'invalid'
    assert body['reason'] == 'revoked'
    assert body['plan'] is None
