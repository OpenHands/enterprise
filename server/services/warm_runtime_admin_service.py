"""Client for runtime-api's warm runtime config endpoints.

Reads use the same ``X-API-Key`` as sandbox creation. Writes need runtime-api's
admin JWT, obtained with its challenge/response login from
``RUNTIME_API_ADMIN_PASSWORD`` (runtime-api's own ``ADMIN_PASSWORD``).
"""

import asyncio
import hashlib
import os
import time
from typing import Any

import httpx

ADMIN_TOKEN_REFRESH_SECONDS = 23 * 60 * 60  # runtime-api issues 24h tokens
REQUEST_TIMEOUT_SECONDS = 15.0


class WarmRuntimeAdminError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _detail_text(response: httpx.Response) -> str:
    try:
        detail = response.json().get('detail')
    except ValueError:
        detail = None
    if isinstance(detail, list):
        return '; '.join(
            f'{".".join(str(p) for p in item.get("loc", [])[1:])}: {item.get("msg")}'
            for item in detail
        )
    return str(detail) if detail else response.text[:200]


class WarmRuntimeAdminService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._login_lock = asyncio.Lock()

    @staticmethod
    def _api_url() -> str:
        url = os.environ.get('SANDBOX_REMOTE_RUNTIME_API_URL', '').rstrip('/')
        if not url or not os.environ.get('SANDBOX_API_KEY'):
            raise WarmRuntimeAdminError(
                503,
                'runtime-api is not configured '
                '(SANDBOX_REMOTE_RUNTIME_API_URL / SANDBOX_API_KEY).',
            )
        return url

    async def _send(
        self, method: str, path: str, headers: dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        url = f'{self._api_url()}/api{path}'
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                return await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise WarmRuntimeAdminError(
                502, f'Could not reach runtime-api: {exc.__class__.__name__}'
            ) from exc

    async def _login(self) -> str:
        password = os.environ.get('RUNTIME_API_ADMIN_PASSWORD')
        if not password:
            raise WarmRuntimeAdminError(
                503,
                'Editing warm runtimes is not enabled: RUNTIME_API_ADMIN_PASSWORD '
                'is not set.',
            )
        challenge_response = await self._send('GET', '/admin/challenge', {})
        if challenge_response.status_code != 200:
            raise WarmRuntimeAdminError(
                502,
                f'runtime-api admin challenge failed: {_detail_text(challenge_response)}',
            )
        challenge = challenge_response.json()
        digest = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            (challenge['salt'] + challenge['challenge']).encode(),
            challenge['iterations'],
            dklen=32,
        ).hex()
        login_response = await self._send(
            'POST',
            '/admin/login',
            {},
            json={'challenge': challenge['challenge'], 'hash': digest},
        )
        if login_response.status_code != 200:
            raise WarmRuntimeAdminError(
                502,
                f'runtime-api admin login failed: {_detail_text(login_response)}',
            )
        return login_response.json()['token']

    async def _admin_token(self, force_refresh: bool = False) -> str:
        async with self._login_lock:
            if (
                force_refresh
                or self._token is None
                or time.monotonic() >= self._token_expires_at
            ):
                self._token = await self._login()
                self._token_expires_at = time.monotonic() + ADMIN_TOKEN_REFRESH_SECONDS
            return self._token

    async def _admin_request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        response = await self._send(
            method,
            path,
            {'Authorization': f'Bearer {await self._admin_token()}'},
            **kwargs,
        )
        if response.status_code == 401:
            response = await self._send(
                method,
                path,
                {'Authorization': f'Bearer {await self._admin_token(True)}'},
                **kwargs,
            )
        return response

    @staticmethod
    def _raise_for_write_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code in (404, 422):
            raise WarmRuntimeAdminError(response.status_code, _detail_text(response))
        raise WarmRuntimeAdminError(
            502,
            f'runtime-api returned {response.status_code}: {_detail_text(response)}',
        )

    async def list_configs(self) -> list[dict[str, Any]]:
        response = await self._send(
            'GET',
            '/warm-runtime-configs',
            {'X-API-Key': os.environ.get('SANDBOX_API_KEY', '')},
        )
        if not response.is_success:
            raise WarmRuntimeAdminError(
                502,
                f'runtime-api returned {response.status_code}: {_detail_text(response)}',
            )
        return response.json()['configs']

    async def save_config(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        response = await self._admin_request(
            'PUT', f'/admin/warm-runtime-configs/{name}', json=config
        )
        self._raise_for_write_error(response)
        return response.json()

    async def delete_config(self, name: str) -> None:
        response = await self._admin_request(
            'DELETE', f'/admin/warm-runtime-configs/{name}'
        )
        self._raise_for_write_error(response)


warm_runtime_admin_service = WarmRuntimeAdminService()
