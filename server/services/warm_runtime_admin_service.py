"""Client for runtime-api's warm runtime config endpoints.

Authenticates with the same ``X-API-Key`` used for sandbox creation, which
runtime-api accepts for managing warm runtime configs.
"""

import os
from typing import Any

import httpx

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

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = os.environ.get('SANDBOX_REMOTE_RUNTIME_API_URL', '').rstrip('/')
        api_key = os.environ.get('SANDBOX_API_KEY')
        if not url or not api_key:
            raise WarmRuntimeAdminError(
                503,
                'runtime-api is not configured '
                '(SANDBOX_REMOTE_RUNTIME_API_URL / SANDBOX_API_KEY).',
            )
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.request(
                    method, f'{url}/api{path}', headers={'X-API-Key': api_key}, **kwargs
                )
        except httpx.HTTPError as exc:
            raise WarmRuntimeAdminError(
                502, f'Could not reach runtime-api: {exc.__class__.__name__}'
            ) from exc
        if response.is_success:
            return response
        if response.status_code in (404, 422):
            raise WarmRuntimeAdminError(response.status_code, _detail_text(response))
        raise WarmRuntimeAdminError(
            502,
            f'runtime-api returned {response.status_code}: {_detail_text(response)}',
        )

    async def list_configs(self) -> list[dict[str, Any]]:
        response = await self._request('GET', '/warm-runtime-configs')
        return response.json()['configs']

    async def save_config(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            'PUT', f'/admin/warm-runtime-configs/{name}', json=config
        )
        return response.json()

    async def delete_config(self, name: str) -> None:
        await self._request('DELETE', f'/admin/warm-runtime-configs/{name}')


warm_runtime_admin_service = WarmRuntimeAdminService()
