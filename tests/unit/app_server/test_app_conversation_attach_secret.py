"""Tests for the live secret-attach endpoint.

Covers the cloud half of issue #761: pushing a secret saved via the Canvas
``request_secret`` modal into a *running* conversation without restarting it,
resolving the value server-side so it never transits the browser a second time.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversation,
    AppConversationAttachSecretRequest,
    AppConversationAttachSecretStatus,
)
from openhands.app_server.app_conversation.app_conversation_router import (
    AGENT_SERVER,
    attach_secret_to_conversation,
)
from openhands.app_server.integrations.provider import CustomSecret
from openhands.app_server.sandbox.sandbox_models import (
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.secrets.secrets_models import Secrets


def _sandbox(
    status: SandboxStatus = SandboxStatus.RUNNING,
    session_api_key: str | None = 'sess-key',
    agent_server_url: str = 'http://agent-server',
) -> SandboxInfo:
    return SandboxInfo(
        id='sb-1',
        created_by_user_id='test-user',
        sandbox_spec_id='spec-1',
        status=status,
        session_api_key=session_api_key,
        exposed_urls=[ExposedUrl(name=AGENT_SERVER, url=agent_server_url, port=1)],
    )


def _make_service(get_conversation_return, sandbox_return) -> tuple:
    service = MagicMock()
    service.get_app_conversation = AsyncMock(return_value=get_conversation_return)
    sandbox_service = MagicMock()
    sandbox_service.get_sandbox = AsyncMock(return_value=sandbox_return)
    return service, sandbox_service


def _make_secrets_store(secret_value: str | None):
    store = MagicMock()
    if secret_value is None:
        loaded = Secrets(custom_secrets={}, provider_tokens={})
    else:
        loaded = Secrets(
            custom_secrets={'MY_SECRET': CustomSecret(secret=SecretStr(secret_value))},
            provider_tokens={},
        )
    store.load_personal = AsyncMock(return_value=loaded)
    return store


@pytest.mark.asyncio
async def test_attach_secret_pushes_value_to_running_conversation():
    conv = AppConversation(
        id=uuid4(),
        created_by_user_id='test-user',
        sandbox_id='sb-1',
        sandbox_status=SandboxStatus.RUNNING,
    )
    service, sandbox_service = _make_service(conv, _sandbox())
    store = _make_secrets_store('super-secret-value')

    captured: dict = {}

    async def fake_post(url, json=None, headers=None, timeout=None):
        captured['url'] = url
        captured['json'] = json
        captured['headers'] = headers
        return httpx.Response(200, request=httpx.Request('POST', url))

    client = MagicMock()
    client.post = fake_post

    resp = await attach_secret_to_conversation(
        conversation_id=conv.id,
        request=AppConversationAttachSecretRequest(secret_name='MY_SECRET'),
        app_conversation_service=service,
        sandbox_service=sandbox_service,
        secrets_store=store,
        httpx_client=client,
    )

    assert resp.status is AppConversationAttachSecretStatus.ATTACHED
    # Value is resolved server-side and sent only to the agent server.
    assert captured['json'] == {'secrets': {'MY_SECRET': 'super-secret-value'}}
    assert captured['headers'] == {'X-Session-API-Key': 'sess-key'}
    assert captured['url'].endswith(f'/api/conversations/{conv.id}/secrets')
    store.load_personal.assert_awaited_once()


@pytest.mark.asyncio
async def test_attach_secret_deferred_when_sandbox_paused():
    conv = AppConversation(
        id=uuid4(),
        created_by_user_id='test-user',
        sandbox_id='sb-1',
        sandbox_status=SandboxStatus.PAUSED,
    )
    service, sandbox_service = _make_service(
        conv, _sandbox(status=SandboxStatus.PAUSED)
    )
    store = _make_secrets_store('super-secret-value')
    client = MagicMock()
    client.post = AsyncMock()

    resp = await attach_secret_to_conversation(
        conversation_id=conv.id,
        request=AppConversationAttachSecretRequest(secret_name='MY_SECRET'),
        app_conversation_service=service,
        sandbox_service=sandbox_service,
        secrets_store=store,
        httpx_client=client,
    )

    # Recoverable, not a false saved; nothing pushed.
    assert resp.status is AppConversationAttachSecretStatus.DEFERRED
    client.post.assert_not_called()
    # Value is never resolved for a non-running sandbox.
    store.load_personal.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_secret_deferred_when_archived():
    conv = AppConversation(
        id=uuid4(),
        created_by_user_id='test-user',
        sandbox_id='sb-1',
        sandbox_status=SandboxStatus.MISSING,
    )
    service, sandbox_service = _make_service(
        conv, _sandbox(status=SandboxStatus.MISSING)
    )
    store = _make_secrets_store('super-secret-value')
    client = MagicMock()
    client.post = AsyncMock()

    resp = await attach_secret_to_conversation(
        conversation_id=conv.id,
        request=AppConversationAttachSecretRequest(secret_name='MY_SECRET'),
        app_conversation_service=service,
        sandbox_service=sandbox_service,
        secrets_store=store,
        httpx_client=client,
    )

    assert resp.status is AppConversationAttachSecretStatus.DEFERRED
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_attach_secret_not_found_does_not_push():
    conv = AppConversation(
        id=uuid4(),
        created_by_user_id='test-user',
        sandbox_id='sb-1',
        sandbox_status=SandboxStatus.RUNNING,
    )
    service, sandbox_service = _make_service(conv, _sandbox())
    store = _make_secrets_store(None)  # no saved secret by that name
    client = MagicMock()
    client.post = AsyncMock()

    resp = await attach_secret_to_conversation(
        conversation_id=conv.id,
        request=AppConversationAttachSecretRequest(secret_name='MISSING'),
        app_conversation_service=service,
        sandbox_service=sandbox_service,
        secrets_store=store,
        httpx_client=client,
    )

    assert resp.status is AppConversationAttachSecretStatus.NOT_FOUND
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_attach_secret_error_on_agent_server_failure():
    conv = AppConversation(
        id=uuid4(),
        created_by_user_id='test-user',
        sandbox_id='sb-1',
        sandbox_status=SandboxStatus.RUNNING,
    )
    service, sandbox_service = _make_service(conv, _sandbox())
    store = _make_secrets_store('super-secret-value')

    async def fake_post(url, json=None, headers=None, timeout=None):
        request = httpx.Request('POST', url)
        return httpx.Response(500, request=request, text='boom')

    client = MagicMock()
    client.post = fake_post

    resp = await attach_secret_to_conversation(
        conversation_id=conv.id,
        request=AppConversationAttachSecretRequest(secret_name='MY_SECRET'),
        app_conversation_service=service,
        sandbox_service=sandbox_service,
        secrets_store=store,
        httpx_client=client,
    )

    assert resp.status is AppConversationAttachSecretStatus.ERROR
    # Never report a false attached on failure.
    assert '500' in (resp.message or '')


@pytest.mark.asyncio
async def test_attach_secret_404_when_conversation_missing():
    service, sandbox_service = _make_service(None, _sandbox())
    store = _make_secrets_store('super-secret-value')
    client = MagicMock()
    client.post = AsyncMock()

    with pytest.raises(Exception) as exc:
        await attach_secret_to_conversation(
            conversation_id=uuid4(),
            request=AppConversationAttachSecretRequest(secret_name='MY_SECRET'),
            app_conversation_service=service,
            sandbox_service=sandbox_service,
            secrets_store=store,
            httpx_client=client,
        )
    assert exc.value.status_code == 404
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_attach_secret_request_carrys_only_name():
    """The request body must never accept a secret value."""
    req = AppConversationAttachSecretRequest(secret_name='MY_SECRET')
    assert req.secret_name == 'MY_SECRET'
    assert 'value' not in req.model_dump()
