"""Sanity check: the race tests in test_secrets_api_race must actually
fail when the lock is removed (proving they exercise the race). We
swap the lock out for a no-op and re-run the create endpoint."""

import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from openhands.app_server.file_store import get_file_store
from openhands.app_server.secrets import secrets_router
from openhands.app_server.secrets.file_secrets_store import FileSecretsStore
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_router import (
    _reset_user_secrets_locks,
)
from openhands.app_server.secrets.secrets_router import (
    router as secrets_router_original,
)


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(secrets_router_original)
    return app


@pytest.fixture
def temp_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp('secrets_store_race_no_lock'))


@pytest.fixture
def file_secrets_store(temp_dir):
    file_store = get_file_store('local', temp_dir)
    store = FileSecretsStore(file_store)
    with patch(
        'openhands.app_server.secrets.file_secrets_store.FileSecretsStore.get_instance',
        AsyncMock(return_value=store),
    ):
        yield store


@pytest.fixture
async def test_client(app):
    with patch.dict(os.environ, {'SESSION_API_KEY': ''}, clear=False):
        with patch('openhands.app_server.utils.dependencies._SESSION_API_KEY', None):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url='http://test'
            ) as client:
                yield client


@pytest.fixture(autouse=True)
def _reset_lock_registry():
    _reset_user_secrets_locks()
    yield
    _reset_user_secrets_locks()


@pytest.mark.asyncio
async def test_concurrent_create_loses_writes_without_lock(
    test_client, file_secrets_store, monkeypatch
):
    """When the lock is replaced with a no-op, the concurrent create
    test must fail. This is a meta-test that proves the lock is what
    protects us from the race, not blind luck."""
    await file_secrets_store.store(Secrets())

    # Inject the race window in the store.
    real_load = file_secrets_store.load

    async def racing_load():
        result = await real_load()
        await asyncio.sleep(0)
        return result

    file_secrets_store.load = racing_load

    # Replace the lock with a no-op.
    @asynccontextmanager
    async def noop_lock(*args, **kwargs):
        yield

    monkeypatch.setattr(secrets_router, '_secrets_write_lock', noop_lock)

    async def post_one(name):
        return await test_client.post(
            '/secrets',
            json={'name': name, 'value': f'value-{name}', 'description': None},
        )

    # With the lock removed, some secrets MUST be lost. We assert the
    # opposite of what the locked test asserts: the stored set must be
    # a strict subset of the posted names.
    secret_names = [f'SECRET_{i:02d}' for i in range(20)]
    results = await asyncio.gather(*(post_one(n) for n in secret_names))
    assert all(r.status_code == 201 for r in results)

    stored = await file_secrets_store.load()
    stored_names = set(stored.custom_secrets.keys())
    assert stored_names < set(secret_names), (
        f'Expected lost writes when lock is removed, but all {len(secret_names)} '
        f'secrets were stored. Race window may not be exercised.'
    )
