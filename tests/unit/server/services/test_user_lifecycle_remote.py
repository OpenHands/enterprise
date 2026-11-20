from unittest.mock import AsyncMock

import httpx
import pytest
from keycloak.exceptions import KeycloakDeleteError, KeycloakPutError

from server.services.user_lifecycle_remote import UserLifecycleRemote


@pytest.fixture(autouse=True)
def keycloak_configured(monkeypatch):
    """Keycloak is configured unless a test says otherwise."""
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.KEYCLOAK_SERVER_URL',
        'https://keycloak.test',
    )


@pytest.mark.asyncio
async def test_missing_keycloak_identity_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=True),
    )
    admin = AsyncMock()
    admin.a_update_user.side_effect = KeycloakPutError(response_code=404)
    admin.a_delete_user.side_effect = KeycloakDeleteError(response_code=404)
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin', lambda: admin
    )
    monkeypatch.setattr('server.services.user_lifecycle_remote.LITE_LLM_API_URL', None)
    remote = UserLifecycleRemote()
    await remote.enable('user')
    await remote.disable('user')
    await remote.delete('user')
    admin.a_delete_user.side_effect = KeycloakDeleteError(response_code=503)
    with pytest.raises(KeycloakDeleteError):
        await remote.delete('user')


@pytest.mark.asyncio
async def test_failed_litellm_listing_is_not_success(monkeypatch):
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=True),
    )
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


@pytest.mark.asyncio
async def test_revokes_all_pages_before_deleting(monkeypatch):
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=True),
    )
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
    import json

    remaining = [str(i) for i in range(205)]
    pages = []

    def handle(request):
        if request.method == 'GET':
            page = int(request.url.params['page'])
            assert request.url.params['size'] == '100'
            pages.append(page)
            return httpx.Response(
                200,
                json={
                    'keys': remaining[(page - 1) * 100 : page * 100],
                    'total_pages': 3,
                },
            )
        for key in json.loads(request.content)['keys']:
            remaining.remove(key)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.httpx.AsyncClient',
        lambda **kwargs: client,
    )
    await UserLifecycleRemote().disable('user')
    assert pages == [1, 2, 3]
    assert remaining == []


@pytest.mark.asyncio
async def test_disabled_litellm_skips_gateway(monkeypatch):
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=True),
    )
    admin = AsyncMock()
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin', lambda: admin
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_URL', 'https://litellm.test'
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_KEY', 'test-only'
    )

    def unexpected_client(**kwargs):
        pytest.fail('disabled gateway must not be contacted')

    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.httpx.AsyncClient', unexpected_client
    )
    await UserLifecycleRemote().disable('user')
    await UserLifecycleRemote().delete('user')
    admin.a_delete_user.assert_awaited_once_with('user')


@pytest.mark.asyncio
async def test_later_missing_page_still_revokes_collected_keys(monkeypatch):
    import json

    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin', lambda: AsyncMock()
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_URL', 'https://litellm.test'
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_KEY', 'test-only'
    )
    deleted = []

    def handle(request):
        if request.method == 'GET':
            if request.url.params['page'] == '2':
                return httpx.Response(404)
            return httpx.Response(200, json={'keys': ['key'], 'total_pages': 2})
        deleted.extend(json.loads(request.content)['keys'])
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.httpx.AsyncClient',
        lambda **kwargs: client,
    )
    await UserLifecycleRemote().disable('user')
    assert deleted == ['key']


def _unexpected_keycloak():
    pytest.fail('Keycloak must not be contacted')


def _litellm_key_listing(monkeypatch, deleted):
    """LiteLLM is on and holds one key for the user; deletions go to ``deleted``."""
    import json

    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_URL', 'https://litellm.test'
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.LITE_LLM_API_KEY', 'test-only'
    )

    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'keys': ['key'], 'total_pages': 1})
        body = json.loads(request.content)
        deleted.extend(body.get('keys') or body.get('user_ids'))
        return httpx.Response(200, json={})

    # Build the client before patching: the patch replaces httpx.AsyncClient.
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.httpx.AsyncClient',
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle)),
    )


@pytest.mark.asyncio
async def test_user_without_idp_identity_skips_keycloak_but_revokes_llm_keys(
    monkeypatch,
):
    """A service account has no Keycloak user; only its LiteLLM state is removed."""
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin',
        _unexpected_keycloak,
    )
    deleted: list[str] = []
    _litellm_key_listing(monkeypatch, deleted)
    remote = UserLifecycleRemote()

    await remote.disable('sa', idp_user=False)
    await remote.enable('sa', idp_user=False)
    await remote.delete('sa', idp_user=False)

    assert deleted == ['key', 'sa']


@pytest.mark.asyncio
async def test_unconfigured_keycloak_is_skipped(monkeypatch):
    """Without KEYCLOAK_SERVER_URL (integrated login only) there is no IdP to update."""
    monkeypatch.setattr('server.services.user_lifecycle_remote.KEYCLOAK_SERVER_URL', '')
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin',
        _unexpected_keycloak,
    )
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=False),
    )
    remote = UserLifecycleRemote()

    await remote.disable('user')
    await remote.enable('user')
    await remote.delete('user')


@pytest.mark.asyncio
async def test_configured_keycloak_still_updates_human_users(monkeypatch):
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.is_litellm_enabled',
        AsyncMock(return_value=False),
    )
    admin = AsyncMock()
    monkeypatch.setattr(
        'server.services.user_lifecycle_remote.get_keycloak_admin', lambda: admin
    )
    remote = UserLifecycleRemote()

    await remote.disable('user')
    await remote.enable('user')
    await remote.delete('user')

    admin.a_update_user.assert_any_await('user', {'enabled': False})
    admin.a_user_logout.assert_awaited_once_with('user')
    admin.a_update_user.assert_any_await('user', {'enabled': True})
    admin.a_delete_user.assert_awaited_once_with('user')
