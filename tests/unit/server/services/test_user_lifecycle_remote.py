from unittest.mock import AsyncMock

import httpx
import pytest
from keycloak.exceptions import KeycloakDeleteError, KeycloakPutError

from server.services.user_lifecycle_remote import UserLifecycleRemote


@pytest.mark.asyncio
async def test_missing_keycloak_identity_is_idempotent(monkeypatch):
    admin = AsyncMock()
    admin.a_update_user.side_effect = KeycloakPutError(response_code=404)
    admin.a_delete_user.side_effect = KeycloakDeleteError(response_code=404)
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin', lambda: admin
    )
    monkeypatch.setattr('server.services.user_lifecycle_remote.LITE_LLM_API_URL', None)
    remote = UserLifecycleRemote()
    await remote.disable('user')
    await remote.delete('user')
    admin.a_delete_user.side_effect = KeycloakDeleteError(response_code=503)
    with pytest.raises(KeycloakDeleteError):
        await remote.delete('user')


@pytest.mark.asyncio
async def test_failed_litellm_listing_is_not_success(monkeypatch):
    admin = AsyncMock()
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin', lambda: admin
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_URL', 'https://litellm.test'
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_KEY', 'test-only'
    )
    # Substitute only the external HTTP transport, retaining real status handling.
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, request=request)
        )
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.httpx.AsyncClient',
        lambda **kwargs: client,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await UserLifecycleRemote().disable('user')
