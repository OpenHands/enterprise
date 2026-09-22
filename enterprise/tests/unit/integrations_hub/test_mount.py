"""Tests for in-process Integrations Hub mounting."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from integrations_hub.mount import (
    INTEGRATIONS_HUB_MOUNT_PATH,
    PrefixedPathASGIApp,
    configure_integrations_hub_env,
    mount_integrations_hub,
)


@pytest.mark.asyncio
async def test_prefixed_path_restores_mount_prefix():
    seen: dict[str, str] = {}

    async def child(scope, receive, send):
        seen['path'] = scope['path']
        await send(
            {
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'text/plain')],
            }
        )
        await send({'type': 'http.response.body', 'body': b'ok'})

    app = PrefixedPathASGIApp(child, INTEGRATIONS_HUB_MOUNT_PATH)
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'GET',
        'scheme': 'http',
        'path': '/integrations',
        'raw_path': b'/integrations',
        'query_string': b'',
        'headers': [],
        'client': ('test', 50000),
        'server': ('test', 80),
        'root_path': INTEGRATIONS_HUB_MOUNT_PATH,
    }

    async def receive():
        return {'type': 'http.disconnect'}

    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    assert seen['path'] == f'{INTEGRATIONS_HUB_MOUNT_PATH}/integrations'
    assert messages[0]['status'] == 200


def test_configure_integrations_hub_env_sets_api_root(monkeypatch):
    monkeypatch.delenv('INTHUB_API_ROOT_PATH', raising=False)
    configure_integrations_hub_env()
    assert os.environ['INTHUB_API_ROOT_PATH'] == INTEGRATIONS_HUB_MOUNT_PATH


@pytest.mark.asyncio
async def test_mount_integrations_hub_serves_live(monkeypatch):
    monkeypatch.setenv('INTHUB_API_ROOT_PATH', INTEGRATIONS_HUB_MOUNT_PATH)
    monkeypatch.setenv('INTHUB_POSTGRES_URL', 'postgresql://unused/db')

    parent = FastAPI()
    mount_integrations_hub(parent)

    transport = ASGITransport(app=parent)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get(f'{INTEGRATIONS_HUB_MOUNT_PATH}/live')

    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_resolve_shared_postgres_url_from_db_env(monkeypatch):
    from integrations_hub.mount import resolve_shared_postgres_url

    monkeypatch.delenv('INTHUB_POSTGRES_URL', raising=False)
    monkeypatch.setenv('DB_HOST', 'db.example')
    monkeypatch.setenv('DB_PORT', '5433')
    monkeypatch.setenv('DB_NAME', 'openhands')
    monkeypatch.setenv('DB_USER', 'oh')
    monkeypatch.setenv('DB_PASS', 's3cret')

    assert (
        resolve_shared_postgres_url()
        == 'postgresql://oh:s3cret@db.example:5433/openhands'
    )


def test_resolve_shared_postgres_url_prefers_inthub_override(monkeypatch):
    from integrations_hub.mount import resolve_shared_postgres_url

    monkeypatch.setenv('INTHUB_POSTGRES_URL', 'postgresql://hub/only')
    monkeypatch.setenv('DB_HOST', 'db.example')

    assert resolve_shared_postgres_url() == 'postgresql://hub/only'


@pytest.mark.asyncio
async def test_hub_not_mounted_when_flag_off_routes_missing():
    """When saas_server skips mount, /api/integrations-hub is absent."""
    app = FastAPI()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get(f'{INTEGRATIONS_HUB_MOUNT_PATH}/integrations')
    assert response.status_code == 404
