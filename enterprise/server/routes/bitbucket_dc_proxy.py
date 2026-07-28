import asyncio
import time

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from server.auth.constants import (
    BITBUCKET_DATA_CENTER_HOST,
    BITBUCKET_DC_CONNECT_TIMEOUT,
    BITBUCKET_DC_USERINFO_TIMEOUT,
)

from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger

router = APIRouter(prefix='/bitbucket-dc-proxy')


def _select_user_data(users: list[dict], username: str) -> dict | None:
    username_folded = username.casefold()
    for user in users:
        for key in ('name', 'emailAddress', 'slug'):
            value = user.get(key)
            if isinstance(value, str) and value.casefold() == username_folded:
                return user

    return users[0] if users else None


def _log_hop_timing(hop: str, started_at: float) -> None:
    logger.info(
        'bitbucket_dc_userinfo_hop',
        extra={
            'hop': hop,
            'elapsed_ms': round((time.monotonic() - started_at) * 1000),
        },
    )


# Bitbucket Data Center is not an OIDC provider, so keycloak
# can't retrieve user info from it directly.
# This endpoint proxies requests to bitbucket data center to get user info
# given a Bitbucket Data Center access token. Keycloak
# is configured to use this endpoint as the User Info Endpoint
# for the Bitbucket Data Center OIDC provider.
@router.get('/oauth2/userinfo')
async def userinfo(request: Request):
    if not BITBUCKET_DATA_CENTER_HOST:
        raise ValueError('BITBUCKET_DATA_CENTER_HOST must be configured')
    bitbucket_base_url = f'https://{BITBUCKET_DATA_CENTER_HOST}'

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JSONResponse({'error': 'missing_token'}, status_code=401)

    headers = {'Authorization': auth_header}
    started_at = time.monotonic()
    try:
        timeout = httpx.Timeout(
            BITBUCKET_DC_USERINFO_TIMEOUT,
            connect=min(
                BITBUCKET_DC_CONNECT_TIMEOUT,
                BITBUCKET_DC_USERINFO_TIMEOUT,
            ),
        )
        async with asyncio.timeout(BITBUCKET_DC_USERINFO_TIMEOUT):
            async with httpx.AsyncClient(
                verify=httpx_verify_option(), timeout=timeout
            ) as client:
                # Step 1: get username
                hop_started_at = time.monotonic()
                whoami_resp = await client.get(
                    f'{bitbucket_base_url}/plugins/servlet/applinks/whoami',
                    headers=headers,
                )
                _log_hop_timing('whoami', hop_started_at)
                if whoami_resp.status_code != 200:
                    return JSONResponse({'error': 'not_authenticated'}, status_code=401)
                username = whoami_resp.text.strip()
                if not username:
                    return JSONResponse({'error': 'not_authenticated'}, status_code=401)

                # Step 2: get user details
                hop_started_at = time.monotonic()
                user_resp = await client.get(
                    f'{bitbucket_base_url}/rest/api/latest/users',
                    headers=headers,
                    params={'filter': username},
                )
                _log_hop_timing('users', hop_started_at)
                if user_resp.status_code != 200:
                    return JSONResponse(
                        {'error': f'bitbucket_error: {user_resp.status_code}'},
                        status_code=user_resp.status_code,
                    )
                user_data = _select_user_data(
                    user_resp.json().get('values', []), username
                )
                if not user_data:
                    return JSONResponse(
                        {'error': f'user_not_found: {username}'},
                        status_code=404,
                    )
    except (TimeoutError, httpx.TimeoutException):
        logger.warning(
            'bitbucket_dc_userinfo_timeout',
            extra={
                'elapsed_ms': round((time.monotonic() - started_at) * 1000),
                'timeout_seconds': BITBUCKET_DC_USERINFO_TIMEOUT,
            },
            exc_info=True,
        )
        return JSONResponse(
            {'error': 'bitbucket_timeout'},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.RequestError:
        logger.warning(
            'bitbucket_dc_userinfo_unavailable',
            extra={
                'elapsed_ms': round((time.monotonic() - started_at) * 1000),
            },
            exc_info=True,
        )
        return JSONResponse(
            {'error': 'bitbucket_unavailable'},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return JSONResponse(
        {
            'sub': str(user_data.get('id', username)),
            'preferred_username': user_data.get('name', username),
            'name': user_data.get('displayName', username),
            'email': user_data.get('emailAddress', ''),
        }
    )
