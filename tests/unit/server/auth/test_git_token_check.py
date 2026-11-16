"""Tests for ``check_git_provider_token``.

The ``oauth_tokens`` store runs against the real test database; only the git
provider's API (``GitService.get_user``) and the token endpoint are mocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.app_server.integrations.provider import ProviderHandler
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderType,
    UnknownException,
)
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import git_token_check
from server.auth.git_token_check import check_git_provider_token
from server.auth.oauth_v2_refresh import RefreshTokenRejectedError
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.oauth_token_store import OAuthTokenStore


@pytest.fixture
def patched_session(async_session_maker):
    jwt_svc = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test-key'), active=True)]
    )
    with (
        patch('storage.oauth_token_store.a_session_maker', async_session_maker),
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
    ):
        yield


@pytest.fixture
async def provider(async_session_maker, patched_session) -> AuthProvider:
    row = AuthProvider(
        title='GitLab',
        is_idp=False,
        enabled=True,
        details=OAuthProviderDetails(
            provider_type=ProviderType.GITLAB,
            client_id='cid',
            token_url='https://gitlab.example.com/oauth/token',
        ),
    )
    async with async_session_maker() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@pytest.fixture
def store(provider, create_user) -> OAuthTokenStore:
    return OAuthTokenStore(user_id=create_user().id, oauth_provider_id=provider.id)


def _patch_get_user(**get_user_kwargs):
    """Mock the provider API; records the token and host the check used."""
    seen: dict = {}
    service = MagicMock()
    service.get_user = AsyncMock(**get_user_kwargs)

    def fake_get_service(handler: ProviderHandler, provider_type: ProviderType):
        token = handler.provider_tokens[provider_type]
        seen['token'] = token.token.get_secret_value() if token.token else None
        seen['host'] = token.host
        return service

    return patch.object(ProviderHandler, 'get_service', fake_get_service), seen


async def _check(store: OAuthTokenStore, provider: AuthProvider):
    return await check_git_provider_token(
        store.user_id, provider, ProviderType.GITLAB, 'gitlab.example.com'
    )


@pytest.mark.asyncio
async def test_no_linked_token(store, provider):
    get_user_patch, seen = _patch_get_user()
    with get_user_patch:
        assert await _check(store, provider) is None
    assert seen == {}


@pytest.mark.asyncio
async def test_valid_token_clears_an_earlier_flag(store, provider):
    await store.store_tokens('at', None, None, None)
    await store.set_invalidated('at', True)

    get_user_patch, seen = _patch_get_user(return_value=MagicMock())
    with get_user_patch:
        assert await _check(store, provider) == 'valid'

    assert seen == {'token': 'at', 'host': 'gitlab.example.com'}
    assert (await store.get_raw()).invalidated_at is None


@pytest.mark.asyncio
async def test_rejected_token_is_flagged(store, provider):
    await store.store_tokens('at', None, None, None)

    get_user_patch, _ = _patch_get_user(
        side_effect=AuthenticationError('Invalid gitlab token')
    )
    with get_user_patch:
        assert await _check(store, provider) == 'invalid'

    assert (await store.get_raw()).invalidated_at is not None


@pytest.mark.parametrize(
    'error',
    [UnknownException('Unknown error: 502'), httpx.ConnectTimeout('timed out')],
)
@pytest.mark.asyncio
async def test_provider_unreachable_changes_nothing(store, provider, error):
    await store.store_tokens('at', None, None, None)

    get_user_patch, _ = _patch_get_user(side_effect=error)
    with get_user_patch:
        assert await _check(store, provider) == 'unavailable'

    assert (await store.get_raw()).invalidated_at is None


@pytest.mark.asyncio
async def test_expired_token_is_refreshed_before_the_call(store, provider):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await store.store_tokens('old-at', 'rt', past, None)

    async def refresh(*_):
        return {
            'access_token': 'new-at',
            'refresh_token': 'new-rt',
            'access_token_expires_at': None,
            'refresh_token_expires_at': None,
        }

    get_user_patch, seen = _patch_get_user(return_value=MagicMock())
    with (
        patch.object(git_token_check, 'make_refresh_callback', return_value=refresh),
        get_user_patch,
    ):
        assert await _check(store, provider) == 'valid'

    assert seen['token'] == 'new-at'


@pytest.mark.asyncio
async def test_rejected_refresh_token_is_flagged(store, provider):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await store.store_tokens('old-at', 'rt', past, None)

    async def refresh(*_):
        raise RefreshTokenRejectedError('invalid_grant')

    get_user_patch, seen = _patch_get_user()
    with (
        patch.object(git_token_check, 'make_refresh_callback', return_value=refresh),
        get_user_patch,
    ):
        assert await _check(store, provider) == 'invalid'

    assert seen == {}
    assert (await store.get_raw()).invalidated_at is not None


@pytest.mark.asyncio
async def test_failed_refresh_for_another_reason_changes_nothing(store, provider):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await store.store_tokens('old-at', 'rt', past, None)

    async def refresh(*_):
        raise ValueError('Token refresh failed: status 503')

    with patch.object(git_token_check, 'make_refresh_callback', return_value=refresh):
        assert await _check(store, provider) == 'unavailable'

    row = await store.get_raw()
    assert row is not None
    assert row.invalidated_at is None
