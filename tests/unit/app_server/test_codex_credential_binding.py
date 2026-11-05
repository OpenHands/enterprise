"""Tests for the Codex credential binding glue (OHE-3289).

- ``activate_codex_credential_binding``: the request the agent server's
  activation route expects, and a refusal that must not block a start.
- ``reactivate_sandbox_codex_credential_bindings``: re-sends the binding for
  the Codex conversations a resumed agent server hosts, and only for those.
"""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from openhands.agent_server.models import ConversationInfo, ConversationPage
from openhands.app_server.app_conversation.codex_credential_binding import (
    activate_codex_credential_binding,
    credential_binding_callback_url,
    reactivate_sandbox_codex_credential_bindings,
)
from openhands.app_server.sandbox.sandbox_models import SandboxInfo, SandboxStatus
from openhands.sdk import LLM, Agent, LocalWorkspace
from openhands.sdk.agent import ACPAgent

AGENT_SERVER_URL = 'http://agent-server:8000'
WEB_URL = 'https://app.example'
SESSION_KEY = 'session-key'
CALLBACK_URL = f'{WEB_URL}/api/v1/sandboxes/sb-1/credential-bindings/CODEX_AUTH_JSON'


def _sandbox() -> SandboxInfo:
    return SandboxInfo(
        id='sb-1',
        created_by_user_id='user-1',
        sandbox_spec_id='spec',
        status=SandboxStatus.RUNNING,
        session_api_key=SESSION_KEY,
    )


def _binding_path(conversation_id: UUID) -> str:
    return f'/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON'


def _conversation(agent) -> ConversationInfo:
    return ConversationInfo(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir='/workspace/project'),
        agent=agent,
    )


@pytest.mark.asyncio
async def test_activate_sends_the_binding_the_agent_server_expects():
    conversation_id = uuid4()
    requests: list[httpx.Request] = []

    def agent_server(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent_server)) as client:
        accepted = await activate_codex_credential_binding(
            client,
            agent_server_url=AGENT_SERVER_URL,
            session_api_key=SESSION_KEY,
            callback_url=credential_binding_callback_url(WEB_URL, 'sb-1'),
            conversation_id=conversation_id,
        )

    assert accepted is True
    (request,) = requests
    assert request.method == 'PUT'
    assert str(request.url) == f'{AGENT_SERVER_URL}{_binding_path(conversation_id)}'
    assert request.headers['X-Session-API-Key'] == SESSION_KEY
    assert json.loads(request.content) == {
        'url': CALLBACK_URL,
        'headers': {'X-Session-API-Key': SESSION_KEY},
    }


@pytest.mark.asyncio
async def test_activate_reports_a_refusal_instead_of_raising():
    refuse = httpx.MockTransport(lambda _: httpx.Response(409))

    async with httpx.AsyncClient(transport=refuse) as client:
        accepted = await activate_codex_credential_binding(
            client,
            agent_server_url=AGENT_SERVER_URL,
            session_api_key=SESSION_KEY,
            callback_url=CALLBACK_URL,
            conversation_id=uuid4(),
        )

    assert accepted is False


@pytest.mark.asyncio
async def test_reactivate_rebinds_only_the_codex_conversations_on_the_sandbox():
    codex = _conversation(ACPAgent(acp_command=['codex-acp'], acp_server='codex'))
    other = _conversation(
        Agent(llm=LLM(model='gpt-4o-mini', api_key=SecretStr('k')), tools=[])
    )
    catalog = ConversationPage(items=[codex, other]).model_dump(mode='json')
    search_keys: list[str | None] = []
    bound: list[str] = []

    def agent_server(request: httpx.Request) -> httpx.Response:
        if request.method == 'GET' and request.url.path == '/api/conversations/search':
            search_keys.append(request.headers.get('X-Session-API-Key'))
            return httpx.Response(200, json=catalog)
        if request.method == 'PUT':
            bound.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent_server)) as client:
        await reactivate_sandbox_codex_credential_bindings(
            client,
            sandbox=_sandbox(),
            agent_server_url=AGENT_SERVER_URL,
            web_url=WEB_URL,
        )

    assert search_keys == [SESSION_KEY]
    assert bound == [_binding_path(codex.id)]


@pytest.mark.asyncio
async def test_reactivate_tolerates_an_unavailable_catalog():
    bound: list[str] = []

    def agent_server(request: httpx.Request) -> httpx.Response:
        if request.method == 'PUT':
            bound.append(request.url.path)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent_server)) as client:
        await reactivate_sandbox_codex_credential_bindings(
            client,
            sandbox=_sandbox(),
            agent_server_url=AGENT_SERVER_URL,
            web_url=WEB_URL,
        )

    assert bound == []
