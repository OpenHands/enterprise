"""Tests for ``POST /api/v1/app-conversations/{id}/fork``.

The conversation records live in the real SaaS info service (PostgreSQL) and
the history in a real filesystem event store. Only the agent server inside the
sandbox is faked, through an ``httpx.MockTransport``.
"""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openhands.agent_server.models import EventPage
from openhands.app_server.app_conversation.app_conversation_models import (
    ACP_SERVER_TAG_KEY,
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
    FORKED_FROM_CONVERSATION_ID_TAG_KEY,
    FORKED_FROM_EVENT_ID_TAG_KEY,
    AppConversation,
    AppConversationForkRequest,
    AppConversationInfo,
    ConversationTrigger,
)
from openhands.app_server.app_conversation.app_conversation_router import (
    fork_conversation,
)
from openhands.app_server.app_conversation.conversation_fork import (
    build_fork_info,
)
from openhands.app_server.event.filesystem_event_service import FilesystemEventService
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, TextContent
from server.utils.saas_app_conversation_info_injector import (
    SaasSQLAppConversationInfoService,
)
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

AGENT_URL = 'http://agent.test'
SESSION_KEY = 'session-key'


def _message(text: str) -> MessageEvent:
    return MessageEvent(
        source='user',
        llm_message=Message(role='user', content=[TextContent(text=text)]),
    )


class FakeAgentServer:
    """The agent server in the source's sandbox, as the app server sees it."""

    def __init__(
        self,
        fork_status: int = 201,
        history: list[MessageEvent] | None = None,
        events_status: int = 200,
    ):
        self.fork_status = fork_status
        self.history = history or []
        self.events_status = events_status
        self.fork_bodies: list[dict] = []
        self.deleted: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers['X-Session-API-Key'] == SESSION_KEY
        path = request.url.path
        if request.method == 'POST' and path.endswith('/fork'):
            body = json.loads(request.content)
            self.fork_bodies.append(body)
            if self.fork_status != 201:
                return httpx.Response(self.fork_status, json={'detail': 'nope'})
            return httpx.Response(201, json={'id': body['id']})
        if request.method == 'GET' and path.endswith('/events/search'):
            if self.events_status != 200:
                return httpx.Response(self.events_status)
            start = int(request.url.params.get('page_id') or 0)
            end = start + 1
            page = EventPage(
                items=self.history[start:end],
                next_page_id=str(end) if end < len(self.history) else None,
            )
            return httpx.Response(200, content=page.model_dump_json())
        if request.method == 'DELETE':
            self.deleted.append(path.rsplit('/', 1)[-1])
            return httpx.Response(200, json={'success': True})
        raise AssertionError(f'Unexpected request {request.method} {path}')


class ConversationLookup:
    """Stands in for the live-status service, which needs a running runtime."""

    def __init__(self, info_service: SaasSQLAppConversationInfoService):
        self.info_service = info_service

    async def get_app_conversation(self, conversation_id: UUID):
        info = await self.info_service.get_app_conversation_info(conversation_id)
        if info is None:
            return None
        return AppConversation(
            **info.model_dump(exclude={'acp_server', 'repositories'}),
            sandbox_status=SandboxStatus.RUNNING,
        )


class SandboxLookup:
    def __init__(self, sandbox: SandboxInfo):
        self.sandbox = sandbox

    async def get_sandbox(self, sandbox_id: str):
        return self.sandbox if sandbox_id == self.sandbox.id else None


def _sandbox(owner_id: str, status: SandboxStatus = SandboxStatus.RUNNING):
    return SandboxInfo(
        id='sandbox-1',
        sandbox_spec_id='spec-1',
        created_by_user_id=owner_id,
        status=status,
        session_api_key=SESSION_KEY,
        exposed_urls=[ExposedUrl(name=AGENT_SERVER, url=AGENT_URL, port=8000)],
    )


@pytest.fixture
def owner(create_user):
    return create_user()


@pytest.fixture
async def info_service(async_session_maker, owner):
    async with async_session_maker() as session:
        yield SaasSQLAppConversationInfoService(
            db_session=session, user_context=SpecifyUserContext(user_id=str(owner.id))
        )


@pytest.fixture
def event_service(tmp_path, owner):
    return FilesystemEventService(
        prefix=tmp_path,
        user_id=str(owner.id),
        app_conversation_info_service=None,
        app_conversation_info_load_tasks={},
    )


@pytest.fixture
async def source(info_service, owner) -> AppConversationInfo:
    info = AppConversationInfo(
        created_by_user_id=str(owner.id),
        sandbox_id='sandbox-1',
        selected_repository='acme/widgets',
        selected_branch='main',
        git_provider=ProviderType.GITHUB,
        title='Fix the parser',
        trigger=ConversationTrigger.GUI,
        llm_model='litellm_proxy/claude',
        tags={
            ACP_SERVER_TAG_KEY: 'codex',
            ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/workspace/project',
            'pinned': 'true',
            'automationid': 'auto-1',
        },
        pr_number=[7],
    )
    await info_service.save_app_conversation_info(info)
    return info


async def _fork(info_service, event_service, owner, agent, request, **overrides):
    async with httpx.AsyncClient(transport=httpx.MockTransport(agent.handle)) as client:
        return await fork_conversation(
            conversation_id=request.source_id,
            fork_request=request.body,
            app_conversation_service=ConversationLookup(info_service),
            app_conversation_info_service=info_service,
            sandbox_service=SandboxLookup(
                overrides.get('sandbox', _sandbox(str(owner.id)))
            ),
            event_service=event_service,
            httpx_client=client,
        )


class _Request:
    def __init__(self, source_id: UUID, from_event_id: str, title: str | None = None):
        self.source_id = source_id
        self.body = AppConversationForkRequest(from_event_id=from_event_id, title=title)


@pytest.mark.asyncio
async def test_fork_is_an_owned_conversation_with_the_branch_history(
    info_service, event_service, owner, source, async_session_maker
):
    history = [_message('first'), _message('second'), _message('third')]
    agent = FakeAgentServer(history=history)
    source_event = _message('source only')
    await event_service.save_event(source.id, source_event)

    fork = await _fork(
        info_service,
        event_service,
        owner,
        agent,
        _Request(source.id, history[-1].id),
    )

    assert agent.fork_bodies == [
        {
            'id': str(fork.id),
            'title': 'Fix the parser (branch)',
            'from_event_id': history[-1].id,
            'tags': {
                FORKED_FROM_CONVERSATION_ID_TAG_KEY: source.id.hex,
                FORKED_FROM_EVENT_ID_TAG_KEY: history[-1].id,
            },
        }
    ]
    stored = await info_service.get_app_conversation_info(fork.id)
    assert stored is not None
    assert stored.created_by_user_id == str(owner.id)
    assert stored.sandbox_id == source.sandbox_id
    assert stored.selected_repository == 'acme/widgets'
    assert stored.selected_branch == 'main'
    assert stored.git_provider == ProviderType.GITHUB
    assert stored.llm_model == 'litellm_proxy/claude'
    assert stored.title == 'Fix the parser (branch)'
    assert stored.parent_conversation_id is None
    assert stored.pr_number == []
    assert stored.tags == {
        ACP_SERVER_TAG_KEY: 'codex',
        ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/workspace/project',
        FORKED_FROM_CONVERSATION_ID_TAG_KEY: source.id.hex,
        FORKED_FROM_EVENT_ID_TAG_KEY: history[-1].id,
    }

    async with async_session_maker() as session:
        orgs = {
            row.conversation_id: row.org_id
            for row in (
                await session.execute(StoredConversationMetadataSaas.__table__.select())
            ).all()
        }
    assert orgs[str(fork.id)] == owner.current_org_id

    fork_events = await event_service.search_events(fork.id)
    assert [e.id for e in fork_events.items] == [e.id for e in history]
    source_events = await event_service.search_events(source.id)
    assert [e.id for e in source_events.items] == [source_event.id]
    stored_source = await info_service.get_app_conversation_info(source.id)
    assert stored_source is not None
    assert stored_source.tags == source.tags


@pytest.mark.asyncio
async def test_fork_uses_requested_title(info_service, event_service, owner, source):
    agent = FakeAgentServer()

    fork = await _fork(
        info_service, event_service, owner, agent, _Request(source.id, 'e1', 'Try B')
    )

    assert fork.title == 'Try B'
    assert agent.fork_bodies[0]['title'] == 'Try B'


@pytest.mark.asyncio
async def test_conversation_of_another_user_is_not_found(
    async_session_maker, event_service, owner, source, create_user
):
    other = create_user()
    agent = FakeAgentServer()
    async with async_session_maker() as session:
        other_service = SaasSQLAppConversationInfoService(
            db_session=session, user_context=SpecifyUserContext(user_id=str(other.id))
        )
        with pytest.raises(HTTPException) as exc:
            await _fork(
                other_service, event_service, other, agent, _Request(source.id, 'e1')
            )

    assert exc.value.status_code == 404
    assert agent.fork_bodies == []


@pytest.mark.asyncio
async def test_paused_sandbox_must_be_resumed_first(
    info_service, event_service, owner, source
):
    agent = FakeAgentServer()

    with pytest.raises(HTTPException) as exc:
        await _fork(
            info_service,
            event_service,
            owner,
            agent,
            _Request(source.id, 'e1'),
            sandbox=_sandbox(str(owner.id), SandboxStatus.PAUSED),
        )

    assert exc.value.status_code == 409
    assert agent.fork_bodies == []
    assert await info_service.count_app_conversation_info() == 1


@pytest.mark.asyncio
async def test_unknown_event_leaves_no_fork_behind(
    info_service, event_service, owner, source
):
    agent = FakeAgentServer(fork_status=404)

    with pytest.raises(HTTPException) as exc:
        await _fork(
            info_service, event_service, owner, agent, _Request(source.id, 'missing')
        )

    assert exc.value.status_code == 404
    fork_id = UUID(agent.fork_bodies[0]['id'])
    assert await info_service.get_app_conversation_info(fork_id) is None
    assert agent.deleted == [fork_id.hex]
    assert await info_service.count_app_conversation_info() == 1


@pytest.mark.asyncio
async def test_history_copy_failure_rolls_the_fork_back(
    info_service, event_service, owner, source
):
    agent = FakeAgentServer(events_status=500)

    with pytest.raises(HTTPException) as exc:
        await _fork(
            info_service, event_service, owner, agent, _Request(source.id, 'e1')
        )

    assert exc.value.status_code == 502
    fork_id = UUID(agent.fork_bodies[0]['id'])
    assert await info_service.get_app_conversation_info(fork_id) is None
    assert agent.deleted == [fork_id.hex]


def test_fork_request_requires_an_event():
    with pytest.raises(ValidationError):
        AppConversationForkRequest(from_event_id='')


def test_default_title_fits_the_agent_server_limit():
    source = AppConversationInfo(
        created_by_user_id='u', sandbox_id='s', title='x' * 200
    )

    fork = build_fork_info(
        source, uuid4(), AppConversationForkRequest(from_event_id='e1')
    )

    assert fork.title is not None
    assert len(fork.title) == 200
    assert fork.title.endswith(' (branch)')
