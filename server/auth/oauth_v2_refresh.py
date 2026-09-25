"""Token refresh callbacks for the OAuth v2 path (Phase 2).

The ``OAuthTokenStore.get_valid_access_token`` refresh callback contract
returns a dict with ``access_token``, ``refresh_token``, and tz-aware
``access_token_expires_at`` / ``refresh_token_expires_at`` datetimes (``None``
for never-expires). These helpers build callbacks that hit the provider's
``token_url`` with ``grant_type=refresh_token`` and normalize the response
into that shape, independent of the legacy ``TokenManager`` / ``auth_tokens``
table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from urllib.parse import parse_qs

import httpx

from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.oauth_provider import OAuthProvider
from storage.oauth_token_store import RefreshResult

# HTTP timeout for external token-endpoint refresh calls.
REFRESH_HTTP_TIMEOUT = 15.0


def _secret_str(provider: OAuthProvider) -> str | None:
    blob = provider.client_secret
    if blob is None:
        return None
    return blob.get('v') if isinstance(blob, dict) else str(blob)


def _parse_token_response(
    token_data: dict,
) -> tuple[str, str | None, datetime | None, datetime | None]:
    """Normalize a provider refresh response.

    Returns ``(access_token, refresh_token, access_expires_at, refresh_expires_at)``.
    ``None`` expiry means "never expires" — mirroring the ``oauth_tokens`` schema.
    """
    access_token = str(token_data['access_token'])
    refresh_token = token_data.get('refresh_token')
    refresh_token = str(refresh_token) if refresh_token else None

    now = datetime.now(timezone.utc)
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    if 'expires_in' in token_data:
        try:
            access_expires_at = now + timedelta(seconds=int(token_data['expires_in']))
        except (TypeError, ValueError):
            pass
    for key in ('refresh_token_expires_in', 'refresh_expires_in'):
        if key in token_data:
            try:
                refresh_expires_at = now + timedelta(seconds=int(token_data[key]))
            except (TypeError, ValueError):
                pass
            break
    return access_token, refresh_token, access_expires_at, refresh_expires_at


async def refresh_oauth_token(
    provider: OAuthProvider, refresh_token: str
) -> dict[str, object]:
    """Exchange a refresh token for a new token pair at the provider's ``token_url``.

    Returns the normalized ``RefreshResult`` dict (datetimes, ``None`` for
    never-expires) expected by ``OAuthTokenStore.get_valid_access_token``.
    """
    token_url = provider.token_url
    if not token_url:
        raise ValueError(f'Provider {provider.id} has no token_url')

    payload: dict[str, str] = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': provider.client_id,
    }
    client_secret = _secret_str(provider)
    if client_secret:
        payload['client_secret'] = client_secret

    async with httpx.AsyncClient(
        verify=httpx_verify_option(), timeout=REFRESH_HTTP_TIMEOUT
    ) as client:
        response = await client.post(
            token_url,
            data=payload,
            headers={'Accept': 'application/json'},
        )
        if response.status_code >= 400:
            logger.warning(
                'oauth_v2 refresh failed provider=%s status=%s body=%s',
                provider.id,
                response.status_code,
                response.text,
            )
            raise ValueError(
                f'Token refresh failed at provider {provider.id}: '
                f'status {response.status_code}'
            )
        content_type = response.headers.get('content-type', '')
        if 'application/json' in content_type:
            token_data = response.json()
        else:
            parsed = parse_qs(response.text)
            token_data = {
                k: (
                    int(v[0])
                    if k
                    in {'expires_in', 'refresh_token_expires_in', 'refresh_expires_in'}
                    else v[0]
                )
                for k, v in parsed.items()
            }

    access_token, new_refresh, access_exp, refresh_exp = _parse_token_response(
        token_data
    )
    return {
        'access_token': access_token,
        'refresh_token': new_refresh if new_refresh else refresh_token,
        'access_token_expires_at': access_exp,
        'refresh_token_expires_at': refresh_exp,
    }


def make_refresh_callback(
    provider: OAuthProvider,
) -> Callable[[str, datetime | None, datetime | None], Awaitable[RefreshResult | None]]:
    """Build a ``RefreshCallback`` bound to ``provider``.

    The callback ignores the expiry arguments (``OAuthTokenStore`` already
    decided a refresh is needed) and delegates to :func:`refresh_oauth_token`.
    """

    async def _callback(
        refresh_token: str,
        _access_expires_at: datetime | None,
        _refresh_expires_at: datetime | None,
    ) -> RefreshResult | None:
        return await refresh_oauth_token(provider, refresh_token)

    return _callback


def create_oauth_v2_cookie_payload(
    user_id: str,
    access_token_expires_at: datetime | None,
    accepted_tos: bool,
) -> dict:
    """Build the signed JWT payload for the ``openhands_auth`` cookie."""
    import time

    return {
        'user_id': user_id,
        'access_token_expires_at': (
            int(access_token_expires_at.timestamp())
            if access_token_expires_at
            else None
        ),
        'accepted_tos': accepted_tos,
        'iat': int(time.time()),
    }


# Cookie Max-Age cap: 30 days, per the Phase 2 spec.
COOKIE_MAX_AGE_CAP_SECONDS = 30 * 24 * 3600


def compute_cookie_max_age(
    refresh_token_expires_at: datetime | None,
) -> int:
    """Cookie ``Max-Age`` = IDP refresh-token expiry, capped at 30 days.

    ``None`` (never-expires) or a non-positive delta falls back to the cap.
    """
    if refresh_token_expires_at is None:
        return COOKIE_MAX_AGE_CAP_SECONDS
    exp = refresh_token_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = int((exp - datetime.now(timezone.utc)).total_seconds())
    if delta <= 0:
        return COOKIE_MAX_AGE_CAP_SECONDS
    return min(delta, COOKIE_MAX_AGE_CAP_SECONDS)


def sign_oauth_v2_cookie(payload: dict, max_age_seconds: int) -> str:
    """Sign the cookie payload as a JWS with the given lifetime."""
    from storage.encrypt_utils import get_jwt_service

    return get_jwt_service().create_jws_token(
        payload, expires_in=timedelta(seconds=max_age_seconds)
    )
