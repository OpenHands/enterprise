"""Tests for OpenSandboxSandboxService.

The OpenSandbox SDK is mocked; the agent server is a fake that records the
requests the service makes to it.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.sandboxes import SandboxState
from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
from openhands.app_server.sandbox import opensandbox_sandbox_service
from openhands.app_server.sandbox.managed_sandbox_service import ProviderOutcome
from openhands.app_server.sandbox.opensandbox_sandbox_service import (
    CREATED_BY_USER_ID_METADATA_KEY,
    MANAGED_METADATA_KEY,
    SANDBOX_SPEC_ID_METADATA_KEY,
    OpenSandboxSandboxService,
)
from openhands.app_server.sandbox.opensandbox_sandbox_spec_service import (
    OpenSandboxSandboxSpecInfo,
)
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_store import (
    OPENSANDBOX_BACKEND,
    StoredSandbox,
    hash_session_api_key,
)
from openhands.app_server.user.user_context import UserContext

TEMPLATE = 'tpl_1234'
INIT_API_KEY = 'template-init-key'
OWNER_ID = 'user-1'
SANDBOX_ID = 'fsb-0b9e2d43-5d1c-4f43-9a4f-2f8b1c0a7e11'
DOMAIN = 'sbx.example.com'
WEB_URL = 'https://app.example.com'
WEBHOOK_URL = 'http://app.internal:3000'
SESSION_API_KEY = 'the-session-key'
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _info(state: str = SandboxState.RUNNING, sandbox_id: str = SANDBOX_ID):
    return SimpleNamespace(id=sandbox_id, status=SimpleNamespace(state=state))


def _not_found() -> SandboxApiException:
    return SandboxApiException('not found', status_code=404)


def _response(status_code: int = 200, payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = str(payload)
    response.json.return_value = payload or {}
    return response


class FakeAgentServer:
    def __init__(self, init_post_status: int = 200):
        self.init_post_status = init_post_status
        self.posts: list[tuple[str, dict, dict]] = []

    async def get(self, url: str, **kwargs):
        assert url.endswith('/api/init'), url
        return _response(200, {'state': 'dormant'})

    async def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs['json'], kwargs.get('headers') or {}))
        return _response(self.init_post_status, {'state': 'ready'})


def _user_context(user_id: str | None = OWNER_ID) -> AsyncMock:
    context = AsyncMock(spec=UserContext)
    context.get_user_id.return_value = user_id
    context.get_default_sandbox_spec_id.return_value = None
    return context


def _service(db_session, httpx_client=None, init_api_key: str | None = INIT_API_KEY):
    spec = OpenSandboxSandboxSpecInfo(
        id=TEMPLATE,
        command=None,
        working_dir='/workspace/project',
        init_api_key=SecretStr(init_api_key) if init_api_key else None,
    )
    return OpenSandboxSandboxService(
        sandbox_spec_service=PresetSandboxSpecService(specs=[spec]),
        user_context=_user_context(),
        httpx_client=httpx_client or FakeAgentServer(),
        db_session=db_session,
        max_num_sandboxes=10,
        api_domain='osb-server:80',
        api_protocol='http',
        api_key='osb-key',
        sandbox_domain=DOMAIN,
        sandbox_url_scheme='https',
        timeout_seconds=3600,
        ready_timeout_seconds=5,
        init_timeout_seconds=5,
        init_poll_interval=0,
        resume_timeout_seconds=1,
        web_url=WEB_URL,
        webhook_base_url=WEBHOOK_URL,
    )


def _stored(session_api_key: str | None = SESSION_API_KEY) -> StoredSandbox:
    return StoredSandbox(
        id=SANDBOX_ID,
        backend=OPENSANDBOX_BACKEND,
        created_by_user_id=OWNER_ID,
        sandbox_spec_id=TEMPLATE,
        session_api_key_hash=(
            hash_session_api_key(session_api_key) if session_api_key else None
        ),
        session_api_key=SecretStr(session_api_key) if session_api_key else None,
        created_at=CREATED_AT,
    )


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def manager():
    mock = MagicMock()
    mock.get_sandbox_info = AsyncMock(return_value=_info())
    mock.list_sandbox_infos = AsyncMock(
        return_value=SimpleNamespace(
            sandbox_infos=[_info()],
            pagination=SimpleNamespace(has_next_page=False),
        )
    )
    mock.pause_sandbox = AsyncMock()
    mock.resume_sandbox = AsyncMock()
    mock.kill_sandbox = AsyncMock()

    @asynccontextmanager
    async def _manager(self):
        yield mock

    with patch.object(OpenSandboxSandboxService, '_manager', _manager):
        yield mock


@pytest.fixture
def sdk():
    sandbox = SimpleNamespace(id=SANDBOX_ID, close=AsyncMock())
    mock = MagicMock()
    mock.create_from_template = AsyncMock(return_value=sandbox)
    with patch.object(opensandbox_sandbox_service, 'Sandbox', mock):
        yield mock


class TestStartSandbox:
    @pytest.mark.asyncio
    async def test_restores_template_and_inits_agent_server(
        self, sdk, manager, db_session
    ):
        agent_server = FakeAgentServer()
        sandbox = await _service(db_session, agent_server).start_sandbox()

        args = sdk.create_from_template.await_args
        assert args.args == (TEMPLATE,)
        assert args.kwargs['metadata'] == {
            MANAGED_METADATA_KEY: 'true',
            SANDBOX_SPEC_ID_METADATA_KEY: TEMPLATE,
            CREATED_BY_USER_ID_METADATA_KEY: OWNER_ID,
        }
        assert sandbox.id == SANDBOX_ID
        assert sandbox.status == SandboxStatus.RUNNING
        urls = {url.name: url.url for url in sandbox.exposed_urls}
        assert urls[AGENT_SERVER] == f'https://{SANDBOX_ID}-8000.{DOMAIN}'
        assert urls[VSCODE].startswith(f'https://{SANDBOX_ID}-8001.{DOMAIN}/?tkn=')

        url, body, headers = agent_server.posts[0]
        assert url == f'https://{SANDBOX_ID}-8000.{DOMAIN}/api/init'
        assert headers == {'X-Init-API-Key': INIT_API_KEY}
        assert body['session_api_keys'] == [sandbox.session_api_key]
        assert body['webhooks'] == [{'base_url': f'{WEBHOOK_URL}/api/v1/webhooks'}]
        assert body['allow_cors_origins'] == [WEB_URL]

        row = (await db_session.execute(select(StoredSandbox))).scalar_one()
        assert row.backend == OPENSANDBOX_BACKEND
        assert row.session_api_key.get_secret_value() == sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_kills_sandbox_when_init_fails(self, sdk, manager, db_session):
        service = _service(db_session, FakeAgentServer(init_post_status=401))

        with pytest.raises(SandboxError, match='rejected the init API key'):
            await service.start_sandbox()

        manager.kill_sandbox.assert_awaited_once_with(SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_needs_init_api_key(self, sdk, manager, db_session):
        with pytest.raises(SandboxError, match='no init API key'):
            await _service(db_session, init_api_key=None).start_sandbox()
        sdk.create_from_template.assert_not_called()


class TestReads:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('state', 'status'),
        [
            (SandboxState.RUNNING, SandboxStatus.RUNNING),
            (SandboxState.PAUSED, SandboxStatus.PAUSED),
            (SandboxState.PENDING, SandboxStatus.STARTING),
            (SandboxState.FAILED, SandboxStatus.ERROR),
            (SandboxState.TERMINATED, SandboxStatus.MISSING),
        ],
    )
    async def test_status_mapping(self, manager, db_session, state, status):
        db_session.add(_stored())
        await db_session.flush()
        manager.get_sandbox_info.return_value = _info(state)

        sandbox = await _service(db_session).get_sandbox(SANDBOX_ID)

        assert sandbox.status == status
        assert (sandbox.exposed_urls is not None) == (status == SandboxStatus.RUNNING)

    @pytest.mark.asyncio
    async def test_unknown_sandbox_is_missing(self, manager, db_session):
        db_session.add(_stored())
        await db_session.flush()
        manager.get_sandbox_info.side_effect = _not_found()

        sandbox = await _service(db_session).get_sandbox(SANDBOX_ID)

        assert sandbox.status == SandboxStatus.MISSING

    @pytest.mark.asyncio
    async def test_search_reads_live_state_by_owner(self, manager, db_session):
        db_session.add(_stored())
        await db_session.flush()

        page = await _service(db_session).search_sandboxes()

        assert [s.status for s in page.items] == [SandboxStatus.RUNNING]
        sandbox_filter = manager.list_sandbox_infos.await_args.args[0]
        assert sandbox_filter.metadata == {
            MANAGED_METADATA_KEY: 'true',
            CREATED_BY_USER_ID_METADATA_KEY: OWNER_ID,
        }


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_pause_running(self, manager, db_session):
        outcome = await _service(db_session)._pause_at_provider(_stored())

        assert outcome == ProviderOutcome.CHANGED
        manager.pause_sandbox.assert_awaited_once_with(SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_pause_paused_is_done(self, manager, db_session):
        manager.get_sandbox_info.return_value = _info(SandboxState.PAUSED)

        outcome = await _service(db_session)._pause_at_provider(_stored())

        assert outcome == ProviderOutcome.ALREADY_DONE
        manager.pause_sandbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_waits_for_running(self, manager, db_session):
        manager.get_sandbox_info.side_effect = [
            _info(SandboxState.PAUSED),
            _info(SandboxState.PENDING),
            _info(SandboxState.RUNNING),
        ]

        outcome = await _service(db_session)._resume_at_provider(_stored())

        assert outcome == ProviderOutcome.CHANGED
        manager.resume_sandbox.assert_awaited_once_with(SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_resume_gone_fails(self, manager, db_session):
        manager.get_sandbox_info.side_effect = _not_found()

        outcome = await _service(db_session)._resume_at_provider(_stored())

        assert outcome == ProviderOutcome.FAILED

    @pytest.mark.asyncio
    async def test_delete_gone_is_done(self, manager, db_session):
        manager.kill_sandbox.side_effect = _not_found()

        await _service(db_session)._delete_at_provider(_stored())

    @pytest.mark.asyncio
    async def test_delete_error_asks_for_retry(self, manager, db_session):
        manager.kill_sandbox.side_effect = SandboxApiException('boom', status_code=500)

        with pytest.raises(SandboxDeleteRetryError):
            await _service(db_session)._delete_at_provider(_stored())
