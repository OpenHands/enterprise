import asyncio
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.token_manager import TokenManager
from storage.auth_token_store import AuthTokenStore
from storage.auth_tokens import AuthTokens


@pytest.fixture
def broker_tokens(monkeypatch):
    service = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test-secret'), active=True)]
    )
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: service)
    monkeypatch.setattr(
        'server.auth.token_manager.KEYCLOAK_SERVER_URL', 'https://keycloak.test'
    )
    client_class = httpx.AsyncClient

    def configure(payload, form_encoded=False):
        def respond(request):
            if form_encoded:
                return httpx.Response(200, text=urlencode(payload))
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(
            'server.auth.token_manager.httpx.AsyncClient',
            lambda **kwargs: client_class(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        )
        return TokenManager()

    return configure


@pytest.mark.asyncio
@pytest.mark.parametrize('form_encoded', [False, True])
@pytest.mark.parametrize('absolute_offset', [-1200, 1800])
async def test_broker_keeps_original_absolute_expiry(
    broker_tokens, form_encoded, absolute_offset
):
    now = int(time.time())
    expiry = now + absolute_offset
    manager = broker_tokens(
        {
            'access_token': 'opaque-provider-token',
            'refresh_token': 'refresh-token',
            'expires_in': 5086,
            'accessTokenExpiration': expiry,
        },
        form_encoded,
    )
    tokens = await manager.get_idp_tokens_from_keycloak(
        'keycloak-token', ProviderType.AZURE_DEVOPS
    )
    assert tokens['access_token_expires_at'] == expiry
    assert tokens['access_token'] == 'opaque-provider-token'


@pytest.mark.asyncio
@pytest.mark.parametrize('absolute_expiry', [None, 0, '0'])
@pytest.mark.parametrize('expires_in', [0, 3600])
async def test_broker_without_absolute_expiry_preserves_relative_lifetime(
    broker_tokens, absolute_expiry, expires_in
):
    payload = {
        'access_token': 'opaque-token',
        'refresh_token': 'refresh-token',
        'expires_in': expires_in,
    }
    if absolute_expiry is not None:
        payload['accessTokenExpiration'] = absolute_expiry
    manager = broker_tokens(payload)
    before = int(time.time())
    tokens = await manager.get_idp_tokens_from_keycloak(
        'keycloak-token', ProviderType.GITHUB
    )
    after = int(time.time())
    if expires_in:
        assert (
            before + expires_in
            <= tokens['access_token_expires_at']
            <= after + expires_in
        )
    else:
        assert tokens['access_token_expires_at'] == 0


@pytest.mark.asyncio
async def test_expired_broker_token_refreshes_once_for_concurrent_requests(
    broker_tokens, async_session_maker, monkeypatch
):
    monkeypatch.setattr('storage.auth_token_store.a_session_maker', async_session_maker)
    now = int(time.time())
    manager = broker_tokens(
        {
            'access_token': 'expired-opaque-token',
            'refresh_token': 'original-refresh',
            'expires_in': 5086,
            'accessTokenExpiration': now - 1200,
        }
    )
    await manager.store_idp_tokens(ProviderType.AZURE_DEVOPS, 'test-user', 'kc-token')

    async def refresh(provider, refresh_token):
        assert provider == ProviderType.AZURE_DEVOPS
        assert refresh_token == 'original-refresh'
        await asyncio.sleep(0.05)
        return {
            'access_token': 'fresh-opaque-token',
            'refresh_token': 'rotated-refresh',
            'access_token_expires_at': now + 3600,
            'refresh_token_expires_at': 0,
        }

    refresh_call = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(manager, '_refresh_token', refresh_call)
    results = await asyncio.gather(
        *(
            manager.get_idp_token_by_user_id('test-user', ProviderType.AZURE_DEVOPS)
            for _ in range(3)
        )
    )
    assert results == ['fresh-opaque-token'] * 3
    refresh_call.assert_awaited_once()
    async with async_session_maker() as session:
        record = (await session.execute(select(AuthTokens))).scalar_one()
        assert record.access_token != 'fresh-opaque-token'
        assert manager.decrypt_text(record.access_token) == 'fresh-opaque-token'
        assert manager.decrypt_text(record.refresh_token) == 'rotated-refresh'
        assert record.access_token_expires_at == now + 3600


@pytest.mark.asyncio
async def test_unexpired_broker_token_is_reused(
    broker_tokens, async_session_maker, monkeypatch
):
    monkeypatch.setattr('storage.auth_token_store.a_session_maker', async_session_maker)
    now = int(time.time())
    manager = broker_tokens(
        {
            'access_token': 'valid-opaque-token',
            'refresh_token': 'refresh-token',
            'expires_in': 5086,
            'accessTokenExpiration': now + 3600,
        }
    )
    await manager.store_idp_tokens(ProviderType.AZURE_DEVOPS, 'test-user', 'kc-token')
    refresh = AsyncMock()
    monkeypatch.setattr(manager, '_refresh_token', refresh)
    assert (
        await manager.get_idp_token_by_user_id('test-user', ProviderType.AZURE_DEVOPS)
        == 'valid-opaque-token'
    )
    refresh.assert_not_awaited()
    store = AuthTokenStore('test-user', ProviderType.AZURE_DEVOPS)
    tokens = await store.load_tokens()
    assert tokens['access_token_expires_at'] == now + 3600
