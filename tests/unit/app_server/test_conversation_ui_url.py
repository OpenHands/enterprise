"""Conversations must expose a browser link that opens them in Agent Canvas.

Agents that delegate work to a sub-agent start it via
``POST /api/v1/app-conversations`` and then read it back with
``GET /api/v1/app-conversations?ids=...``. The link they hand the user has to
come from that response (``conversation_ui_url``) and open
``/canvas/conversations/<id>``, not the legacy ``/conversations/<id>`` route
or the agent-server API URL in ``conversation_url``.
"""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openhands.app_server import config as app_server_config
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    LiveStatusAppConversationService,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext

WEB_URL = 'https://app.example.com'
AGENT_SERVER_URL = 'https://runtime-abc.example.com'
CONVERSATION_ID = UUID('11111111-1111-1111-1111-111111111111')


@pytest.fixture
def set_web_url(monkeypatch):
    def _set(web_url: str | None) -> None:
        monkeypatch.setattr(
            app_server_config,
            '_global_config',
            app_server_config.get_global_config().model_copy(
                update={'web_url': web_url}
            ),
        )

    return _set


def _running_sandbox(sandbox_id: str) -> SandboxInfo:
    return SandboxInfo(
        id=sandbox_id,
        created_by_user_id=None,
        sandbox_spec_id='spec',
        status=SandboxStatus.RUNNING,
        session_api_key='sk',
        exposed_urls=[ExposedUrl(name=AGENT_SERVER, url=AGENT_SERVER_URL, port=443)],
    )


def _stripped_service() -> LiveStatusAppConversationService:
    return LiveStatusAppConversationService.__new__(LiveStatusAppConversationService)


class _StaticSandboxService:
    """Stands in for the runtime API, which provisions real sandboxes."""

    def __init__(self, sandboxes: list[SandboxInfo]):
        self._by_id = {sandbox.id: sandbox for sandbox in sandboxes}

    async def batch_get_sandboxes(
        self, sandbox_ids: list[str]
    ) -> list[SandboxInfo | None]:
        return [self._by_id.get(sandbox_id) for sandbox_id in sandbox_ids]


@pytest.mark.asyncio
async def test_delegated_conversation_links_to_agent_canvas(async_engine, set_web_url):
    set_web_url(WEB_URL)
    # Stored the way an API-started delegation is: no trigger, no tags, no parent.
    delegated = AppConversationInfo(
        id=uuid4(), created_by_user_id=None, sandbox_id='sandbox-delegated'
    )
    session_maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        info_service = SQLAppConversationInfoService(
            db_session=session, user_context=SpecifyUserContext(user_id=None)
        )
        await info_service.save_app_conversation_info(delegated)

        service = _stripped_service()
        service.app_conversation_info_service = info_service
        service.sandbox_service = _StaticSandboxService(
            [_running_sandbox('sandbox-delegated')]
        )
        service.httpx_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[None]))
        )

        [conversation] = await service.batch_get_app_conversations([delegated.id])

    assert conversation is not None
    assert conversation.conversation_ui_url == (
        f'{WEB_URL}/canvas/conversations/{delegated.id.hex}'
    )
    assert conversation.conversation_url == (
        f'{AGENT_SERVER_URL}/api/conversations/{delegated.id.hex}'
    )


def test_ui_url_does_not_depend_on_a_running_sandbox(set_web_url):
    set_web_url(WEB_URL)
    info = AppConversationInfo(
        id=CONVERSATION_ID, created_by_user_id=None, sandbox_id='sandbox-gone'
    )

    conversation = _stripped_service()._build_conversation(info, None, None)

    assert conversation is not None
    assert conversation.conversation_url is None
    assert conversation.conversation_ui_url == (
        f'{WEB_URL}/canvas/conversations/{CONVERSATION_ID.hex}'
    )


def test_ui_url_tolerates_trailing_slash_in_web_url(set_web_url):
    set_web_url(f'{WEB_URL}/')
    info = AppConversationInfo(
        id=CONVERSATION_ID, created_by_user_id=None, sandbox_id='sandbox-a'
    )

    conversation = _stripped_service()._build_conversation(info, None, None)

    assert conversation is not None
    assert conversation.conversation_ui_url == (
        f'{WEB_URL}/canvas/conversations/{CONVERSATION_ID.hex}'
    )


def test_ui_url_ignores_sandbox_callback_web_url(set_web_url):
    # Without a configured web URL the service falls back to a docker-internal
    # callback address for sandboxes; that is not reachable from a browser.
    set_web_url(None)
    service = _stripped_service()
    service.web_url = 'http://host.docker.internal:3000'
    info = AppConversationInfo(
        id=CONVERSATION_ID, created_by_user_id=None, sandbox_id='sandbox-a'
    )

    conversation = service._build_conversation(info, None, None)

    assert conversation is not None
    assert conversation.conversation_ui_url is None
