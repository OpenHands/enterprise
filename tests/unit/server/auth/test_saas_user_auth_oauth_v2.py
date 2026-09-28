"""Tests for the OAuth v2 cookie auth path in ``SaasUserAuth`` (Phase 2, OHE-3295).

Covers:
- ``saas_user_auth_from_oauth_v2_cookie`` — cookie decode, user lookup,
  accepted_tos propagation, blacklist enforcement.
- ``get_access_token`` v2 path — IDP token resolution via ``OAuthTokenStore``,
  refresh trigger, ``refreshed`` flag, expiry update.
- ``get_provider_tokens`` v2 path — git-provider token resolution from
  ``oauth_tokens`` with refresh callback, ``ProviderToken`` mapping.
- Dual-cookie fallback: ``saas_user_auth_from_cookie`` tries v2 first, then
  legacy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user_auth.user_auth import AuthType
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.auth_error import AuthError
from server.auth.saas_user_auth import (
    SaasUserAuth,
    saas_user_auth_from_cookie,
    saas_user_auth_from_oauth_v2_cookie,
)
from storage.user_authorization import UserAuthorizationType


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(kid='test', key=SecretStr('test-secret-key'), active=True)
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


def _make_v2_cookie(
    jwt_svc: JwtService,
    *,
    user_id: str,
    access_token_expires_at: datetime | None = None,
    accepted_tos: bool = True,
) -> str:
    import time

    payload = {
        'user_id': user_id,
        'access_token_expires_at': (
            int(access_token_expires_at.timestamp())
            if access_token_expires_at
            else None
        ),
        'accepted_tos': accepted_tos,
        'iat': int(time.time()),
    }
    return jwt_svc.create_jws_token(payload, expires_in=timedelta(days=30))


# ── saas_user_auth_from_oauth_v2_cookie ──────────────────────────────────


@pytest.mark.asyncio
async def test_v2_cookie_decode_builds_auth(jwt_svc):
    user_id = str(uuid4())
    cookie = _make_v2_cookie(jwt_svc, user_id=user_id, accepted_tos=True)
    mock_user = MagicMock()
    mock_user.email = 'a@b.com'
    mock_user.email_verified = True

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            new=AsyncMock(return_value=mock_user),
        ),
        patch(
            'server.auth.saas_user_auth.UserAuthorizationStore.get_authorization_type',
            new=AsyncMock(return_value=None),
        ),
    ):
        auth = await saas_user_auth_from_oauth_v2_cookie(cookie)

    assert auth.oauth_v2_cookie is True
    assert auth.user_id == user_id
    assert auth.auth_type == AuthType.COOKIE
    assert auth.accepted_tos is True
    assert auth.email == 'a@b.com'
    assert auth.access_token is None  # not carried in cookie


@pytest.mark.asyncio
async def test_v2_cookie_missing_user_id_raises(jwt_svc):
    # Build a cookie without user_id by crafting the payload directly.
    payload = {'accepted_tos': True, 'iat': 0}
    cookie = jwt_svc.create_jws_token(payload, expires_in=timedelta(days=1))
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        with pytest.raises(AuthError, match='missing user_id'):
            await saas_user_auth_from_oauth_v2_cookie(cookie)


@pytest.mark.asyncio
async def test_v2_cookie_blacklisted_email_raises(jwt_svc):
    user_id = str(uuid4())
    cookie = _make_v2_cookie(jwt_svc, user_id=user_id)
    mock_user = MagicMock()
    mock_user.email = 'bad@evil.com'
    mock_user.email_verified = True

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            new=AsyncMock(return_value=mock_user),
        ),
        patch(
            'server.auth.saas_user_auth.UserAuthorizationStore.get_authorization_type',
            new=AsyncMock(return_value=UserAuthorizationType.BLACKLIST),
        ),
    ):
        with pytest.raises(AuthError, match='Access denied'):
            await saas_user_auth_from_oauth_v2_cookie(cookie)


@pytest.mark.asyncio
async def test_v2_cookie_access_token_expires_at_parsed(jwt_svc):
    user_id = str(uuid4())
    ate = datetime.now(timezone.utc) + timedelta(hours=1)
    cookie = _make_v2_cookie(
        jwt_svc, user_id=user_id, access_token_expires_at=ate, accepted_tos=False
    )
    mock_user = MagicMock()
    mock_user.email = None
    mock_user.email_verified = False

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            new=AsyncMock(return_value=mock_user),
        ),
    ):
        auth = await saas_user_auth_from_oauth_v2_cookie(cookie)

    assert auth.access_token_expires_at is not None
    # Within a few seconds of the original (both tz-aware).
    delta = abs((auth.access_token_expires_at - ate).total_seconds())
    assert delta < 2


# ── saas_user_auth_from_cookie dual-cookie fallback ─────────────────────


@pytest.mark.asyncio
async def test_from_cookie_prefers_v2(jwt_svc):
    """When both cookies are present, the v2 cookie wins."""
    user_id = str(uuid4())
    v2_cookie = _make_v2_cookie(jwt_svc, user_id=user_id)
    mock_user = MagicMock()
    mock_user.email = 'a@b.com'
    mock_user.email_verified = True

    request = MagicMock()
    request.cookies = {
        'openhands_auth': v2_cookie,
        'keycloak_auth': 'legacy-cookie',
    }

    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            new=AsyncMock(return_value=mock_user),
        ),
        patch(
            'server.auth.saas_user_auth.UserAuthorizationStore.get_authorization_type',
            new=AsyncMock(return_value=None),
        ),
    ):
        auth = await saas_user_auth_from_cookie(request)

    assert auth is not None
    assert auth.oauth_v2_cookie is True
    assert auth.user_id == user_id


@pytest.mark.asyncio
async def test_from_cookie_falls_back_to_legacy(jwt_svc):
    """Without a v2 cookie, the legacy chunked cookie is used."""
    request = MagicMock()
    request.cookies = {'keycloak_auth': 'legacy-chunked'}

    # Patch the legacy signed-token reader so we don't need a real Keycloak JWT.
    legacy_auth = SaasUserAuth(
        user_id='legacy-user',
        refresh_token=SecretStr('rt'),
        auth_type=AuthType.COOKIE,
    )
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch(
            'server.auth.saas_user_auth.read_chunked_cookie',
            return_value='legacy-chunked',
        ),
        patch(
            'server.auth.saas_user_auth.saas_user_auth_from_signed_token',
            new=AsyncMock(return_value=legacy_auth),
        ),
    ):
        auth = await saas_user_auth_from_cookie(request)

    assert auth is not None
    assert auth.oauth_v2_cookie is False
    assert auth.user_id == 'legacy-user'


@pytest.mark.asyncio
async def test_from_cookie_no_cookie_returns_none(jwt_svc):
    request = MagicMock()
    request.cookies = {}
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        auth = await saas_user_auth_from_cookie(request)
    assert auth is None


# ── get_access_token v2 path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_v2_get_access_token_refreshes_and_sets_flag(jwt_svc):
    """When the IDP token is expired, get_access_token refreshes and sets refreshed=True."""
    user_id = str(uuid4())
    auth = SaasUserAuth(
        user_id=user_id,
        refresh_token=SecretStr(''),
        auth_type=AuthType.COOKIE,
        oauth_v2_cookie=True,
        # Expired → triggers refresh.
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    provider = MagicMock()
    provider.id = 1
    provider.permitted_drift_seconds = 60

    raw_row = MagicMock()
    raw_row.access_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    raw_row.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    token_store = MagicMock()
    token_store.get_raw = AsyncMock(return_value=raw_row)

    async def _fake_get_valid(*, permitted_drift_seconds, refresh):
        # Simulate a refresh: invoke the callback so the refreshed flag is set.
        if refresh is not None:
            await refresh('old-rt', None, None)
        return 'fresh-at'

    token_store.get_valid_access_token = AsyncMock(side_effect=_fake_get_valid)

    with (
        patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
            new=AsyncMock(return_value=[provider]),
        ),
        patch(
            'storage.oauth_token_store.OAuthTokenStore',
            return_value=token_store,
        ),
        patch(
            'server.auth.oauth_v2_refresh.refresh_oauth_token',
            new=AsyncMock(
                return_value={'access_token': 'fresh-at', 'refresh_token': 'rt'}
            ),
        ),
    ):
        token = await auth.get_access_token()

    assert token is not None
    assert token.get_secret_value() == 'fresh-at'
    assert auth.refreshed is True
    assert auth.access_token_expires_at is not None


@pytest.mark.asyncio
async def test_v2_get_access_token_no_refresh_when_valid(jwt_svc):
    """When the IDP token is still valid, no refresh occurs."""
    user_id = str(uuid4())
    auth = SaasUserAuth(
        user_id=user_id,
        refresh_token=SecretStr(''),
        auth_type=AuthType.COOKIE,
        oauth_v2_cookie=True,
        # Still valid (expires in the future).
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    provider = MagicMock()
    provider.id = 1
    provider.permitted_drift_seconds = 60

    raw_row = MagicMock()
    raw_row.access_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    raw_row.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    token_store = MagicMock()
    token_store.get_raw = AsyncMock(return_value=raw_row)
    token_store.get_valid_access_token = AsyncMock(return_value='valid-at')

    with (
        patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_idp_providers',
            new=AsyncMock(return_value=[provider]),
        ),
        patch(
            'storage.oauth_token_store.OAuthTokenStore',
            return_value=token_store,
        ),
    ):
        token = await auth.get_access_token()

    assert token.get_secret_value() == 'valid-at'
    assert auth.refreshed is False


# ── get_provider_tokens v2 path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_v2_get_provider_tokens_builds_mapping(jwt_svc):
    """The v2 path resolves git-provider tokens from oauth_tokens."""
    user_id = str(uuid4())
    auth = SaasUserAuth(
        user_id=user_id,
        refresh_token=SecretStr(''),
        auth_type=AuthType.COOKIE,
        oauth_v2_cookie=True,
    )

    git_provider = MagicMock()
    git_provider.id = 2
    git_provider.provider_category = 'github'
    git_provider.permitted_drift_seconds = 60

    raw_row = MagicMock()
    token_store = MagicMock()
    token_store.get_raw = AsyncMock(return_value=raw_row)
    token_store.get_valid_access_token = AsyncMock(return_value='gh-token')

    # Empty secrets so host resolution uses defaults.
    mock_secrets = MagicMock()
    mock_secrets.provider_tokens = {}

    with (
        patch(
            'storage.oauth_provider_store.OAuthProviderStore.get_git_providers',
            new=AsyncMock(return_value=[git_provider]),
        ),
        patch(
            'storage.oauth_token_store.OAuthTokenStore',
            return_value=token_store,
        ),
        patch.object(auth, 'get_secrets', new=AsyncMock(return_value=mock_secrets)),
        patch(
            'server.auth.oauth_v2_refresh.make_refresh_callback',
            return_value=AsyncMock(),
        ),
    ):
        result = await auth.get_provider_tokens()

    assert result is not None
    from openhands.app_server.integrations.service_types import ProviderType

    assert ProviderType.GITHUB in result
    assert result[ProviderType.GITHUB].token.get_secret_value() == 'gh-token'
