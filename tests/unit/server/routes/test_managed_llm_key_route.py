"""Route tests for ``GET /api/keys/llm/managed/current`` (#5189).

This endpoint is called from inside the agent-server sandbox when a managed-proxy
LLM hits a 401, so the key can be re-resolved and the request retried in place.
The status-code branches are the contract that lets the SDK decide whether to
surface the original 401 or retry:

- 200 + raw key body -> retry with the fresh key
- 404 (no managed key: non-managed / BYOK / none producible) -> let the original
  401 surface instead of masking it
- 401 (sandbox has no owning user) -> auth failure
- 500 (resolve blew up) -> genuine server error

The 404-vs-500 distinction matters most: it is what keeps a "no key for you"
answer from looking like a broken server.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from server.routes.api_keys import get_managed_llm_key_for_sandbox


def _request() -> Request:
    return Request(
        {
            'type': 'http',
            'headers': [(b'x-session-api-key', b'sess-abc')],
        }
    )


def _sandbox(user_id: str | None):
    return SimpleNamespace(id='sb-1', created_by_user_id=user_id)


def _get_instance(resolve: AsyncMock) -> AsyncMock:
    store = SimpleNamespace(resolve_valid_managed_llm_key=resolve)
    return AsyncMock(return_value=store)


@pytest.mark.asyncio
async def test_returns_key_as_plain_text_when_available():
    resolve = AsyncMock(return_value='the-managed-key')
    with (
        patch(
            'server.routes.api_keys.validate_session_key',
            new=AsyncMock(return_value=_sandbox('user-1')),
        ),
        patch(
            'server.routes.api_keys.SaasSettingsStore.get_instance',
            new=_get_instance(resolve),
        ),
    ):
        response = await get_managed_llm_key_for_sandbox(_request())

    assert response.status_code == status.HTTP_200_OK
    assert response.body == b'the-managed-key'
    resolve.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_returns_404_when_no_managed_key():
    """Non-managed / BYOK / none-producible -> 404 so the caller lets the 401 surface."""
    resolve = AsyncMock(return_value=None)
    with (
        patch(
            'server.routes.api_keys.validate_session_key',
            new=AsyncMock(return_value=_sandbox('user-1')),
        ),
        patch(
            'server.routes.api_keys.SaasSettingsStore.get_instance',
            new=_get_instance(resolve),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_managed_llm_key_for_sandbox(_request())

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_returns_401_when_sandbox_has_no_owning_user():
    resolve = AsyncMock()
    with (
        patch(
            'server.routes.api_keys.validate_session_key',
            new=AsyncMock(return_value=_sandbox(None)),
        ),
        patch(
            'server.routes.api_keys.SaasSettingsStore.get_instance',
            new=_get_instance(resolve),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_managed_llm_key_for_sandbox(_request())

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_500_when_resolve_raises():
    """A genuine failure in resolve is a 500 -- distinct from the 404 no-key case."""
    resolve = AsyncMock(side_effect=RuntimeError('boom'))
    with (
        patch(
            'server.routes.api_keys.validate_session_key',
            new=AsyncMock(return_value=_sandbox('user-1')),
        ),
        patch(
            'server.routes.api_keys.SaasSettingsStore.get_instance',
            new=_get_instance(resolve),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_managed_llm_key_for_sandbox(_request())

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_propagates_invalid_session_key_401():
    """An invalid/missing session key from validate_session_key stays a 401."""
    with patch(
        'server.routes.api_keys.validate_session_key',
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid session API key',
            )
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_managed_llm_key_for_sandbox(_request())

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
