"""Tests for the OAuth v2 refresh helpers and cookie utilities (Phase 2, OHE-3295).

Covers:
- ``refresh_oauth_token`` / ``make_refresh_callback`` — token endpoint call +
  normalization (JSON and form-encoded responses, never-expires handling).
- ``compute_cookie_max_age`` — refresh-token expiry → 30-day cap, ``None``
  fallback, non-positive delta fallback.
- ``create_oauth_v2_cookie_payload`` / ``sign_oauth_v2_cookie`` roundtrip.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import oauth_v2_refresh
from storage.oauth_provider import OAuthProvider


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(kid='test', key=SecretStr('test-secret-key'), active=True)
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


def _fake_provider(*, token_url='https://example.com/token', client_secret='sec'):
    provider = MagicMock(spec=OAuthProvider)
    provider.id = 1
    provider.client_id = 'cid'
    provider.client_secret = {'v': client_secret} if client_secret else None
    provider.token_url = token_url
    provider.permitted_drift_seconds = 60
    return provider


def _patch_httpx(token_response: dict, content_type='application/json'):
    response = MagicMock()
    response.status_code = 200
    response.headers = {'content-type': content_type}
    response.json.return_value = token_response
    if content_type != 'application/json':
        from urllib.parse import urlencode

        response.text = urlencode(token_response)
    client_mock = AsyncMock()
    client_mock.post = AsyncMock(return_value=response)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)
    return patch(
        'server.auth.oauth_v2_refresh.httpx.AsyncClient', return_value=client_mock
    )


# ── refresh_oauth_token ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_oauth_token_json_response(jwt_svc):
    provider = _fake_provider()
    token_response = {
        'access_token': 'new-at',
        'refresh_token': 'new-rt',
        'expires_in': 3600,
        'refresh_token_expires_in': 86400,
    }
    with _patch_httpx(token_response):
        result = await oauth_v2_refresh.refresh_oauth_token(provider, 'old-rt')
    assert result['access_token'] == 'new-at'
    assert result['refresh_token'] == 'new-rt'
    assert result['access_token_expires_at'] is not None
    assert result['refresh_token_expires_at'] is not None
    # Datetimes should be tz-aware and in the future.
    now = datetime.now(timezone.utc)
    assert result['access_token_expires_at'] > now
    assert result['refresh_token_expires_at'] > result['access_token_expires_at']


@pytest.mark.asyncio
async def test_refresh_oauth_token_form_encoded_response(jwt_svc):
    provider = _fake_provider()
    token_response = {
        'access_token': 'new-at',
        'refresh_token': 'new-rt',
        'expires_in': '3600',
    }
    with _patch_httpx(token_response, content_type='application/x-www-form-urlencoded'):
        result = await oauth_v2_refresh.refresh_oauth_token(provider, 'old-rt')
    assert result['access_token'] == 'new-at'
    assert result['refresh_token'] == 'new-rt'
    assert result['access_token_expires_at'] is not None


@pytest.mark.asyncio
async def test_refresh_oauth_token_never_expires(jwt_svc):
    """No expires_in → None expiry (never expires)."""
    provider = _fake_provider()
    token_response = {'access_token': 'at', 'refresh_token': 'rt'}
    with _patch_httpx(token_response):
        result = await oauth_v2_refresh.refresh_oauth_token(provider, 'old-rt')
    assert result['access_token_expires_at'] is None
    assert result['refresh_token_expires_at'] is None


@pytest.mark.asyncio
async def test_refresh_oauth_token_no_new_refresh_keeps_old(jwt_svc):
    """If the provider does not return a new refresh_token, keep the old one."""
    provider = _fake_provider()
    token_response = {'access_token': 'at', 'expires_in': 3600}
    with _patch_httpx(token_response):
        result = await oauth_v2_refresh.refresh_oauth_token(provider, 'old-rt')
    assert result['refresh_token'] == 'old-rt'


@pytest.mark.asyncio
async def test_refresh_oauth_token_failure_raises(jwt_svc):
    provider = _fake_provider()
    response = MagicMock()
    response.status_code = 400
    response.text = 'bad request'
    client_mock = AsyncMock()
    client_mock.post = AsyncMock(return_value=response)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)
    with patch(
        'server.auth.oauth_v2_refresh.httpx.AsyncClient', return_value=client_mock
    ):
        with pytest.raises(ValueError, match='Token refresh failed'):
            await oauth_v2_refresh.refresh_oauth_token(provider, 'old-rt')


@pytest.mark.asyncio
async def test_make_refresh_callback_delegates(jwt_svc):
    provider = _fake_provider()
    token_response = {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600}
    with _patch_httpx(token_response):
        cb = oauth_v2_refresh.make_refresh_callback(provider)
        result = await cb('old-rt', None, None)
    assert result is not None
    assert result['access_token'] == 'at'


# ── compute_cookie_max_age ───────────────────────────────────────────────


def test_compute_cookie_max_age_none_returns_cap():
    assert (
        oauth_v2_refresh.compute_cookie_max_age(None)
        == oauth_v2_refresh.COOKIE_MAX_AGE_CAP_SECONDS
    )


def test_compute_cookie_max_age_future_expiry():
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    age = oauth_v2_refresh.compute_cookie_max_age(exp)
    # ~7 days, well under the 30-day cap.
    assert 6 * 24 * 3600 < age <= 7 * 24 * 3600


def test_compute_cookie_max_age_capped_at_30_days():
    exp = datetime.now(timezone.utc) + timedelta(days=365)
    age = oauth_v2_refresh.compute_cookie_max_age(exp)
    assert age == oauth_v2_refresh.COOKIE_MAX_AGE_CAP_SECONDS


def test_compute_cookie_max_age_naive_datetime_treated_as_utc():
    naive = datetime.utcnow() + timedelta(days=7)
    age = oauth_v2_refresh.compute_cookie_max_age(naive)
    assert 6 * 24 * 3600 < age <= 7 * 24 * 3600


def test_compute_cookie_max_age_past_expiry_returns_cap():
    exp = datetime.now(timezone.utc) - timedelta(hours=1)
    age = oauth_v2_refresh.compute_cookie_max_age(exp)
    assert age == oauth_v2_refresh.COOKIE_MAX_AGE_CAP_SECONDS


# ── cookie payload roundtrip ─────────────────────────────────────────────


def test_cookie_payload_roundtrip(jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        ate = datetime.now(timezone.utc) + timedelta(hours=1)
        payload = oauth_v2_refresh.create_oauth_v2_cookie_payload(
            'user-123', ate, accepted_tos=True
        )
        signed = oauth_v2_refresh.sign_oauth_v2_cookie(payload, max_age_seconds=3600)
        decoded = jwt_svc.verify_jws_token(signed)
    assert decoded['user_id'] == 'user-123'
    assert decoded['accepted_tos'] is True
    assert decoded['access_token_expires_at'] is not None


def test_cookie_payload_none_expiry(jwt_svc):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        payload = oauth_v2_refresh.create_oauth_v2_cookie_payload(
            'user-123', None, accepted_tos=False
        )
        signed = oauth_v2_refresh.sign_oauth_v2_cookie(payload, max_age_seconds=3600)
        decoded = jwt_svc.verify_jws_token(signed)
    assert decoded['access_token_expires_at'] is None
    assert decoded['accepted_tos'] is False
