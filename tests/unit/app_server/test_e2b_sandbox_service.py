"""Tests for E2BSandboxService.

The E2B SDK is mocked throughout; the agent server is replaced by a fake that
records the requests the service makes to it. Focus areas:
- the sandbox table as the store for ownership, spec identity and the session key
- user scoping, including cross user isolation and the admin (no user id) case
- the /api/init handshake start_sandbox completes before returning
- lifecycle and status mapping onto the E2B SDK, including MISSING
- exposed URL construction and VSCode URL resolution
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from e2b import (
    AuthenticationException,
    SandboxException,
    SandboxNotFoundException,
    SandboxState,
)
from pydantic import SecretStr
from sqlalchemy import select, text

from openhands.agent_server.init_router import InitRequest
from openhands.app_server.errors import (
    AuthError,
    SandboxDeleteRetryError,
    SandboxError,
)
from openhands.app_server.sandbox import e2b_sandbox_service
from openhands.app_server.sandbox.e2b_sandbox_service import (
    CREATED_BY_USER_ID_METADATA_KEY,
    MANAGED_METADATA_KEY,
    SANDBOX_SPEC_ID_METADATA_KEY,
    WORKER_1_PORT,
    WORKER_2_PORT,
    E2BSandboxService,
)
from openhands.app_server.sandbox.e2b_sandbox_spec_service import E2BSandboxSpecInfo
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    WORKER_1,
    WORKER_2,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_store import (
    E2B_BACKEND,
    StoredSandbox,
    hash_session_api_key,
)
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext

DOMAIN = 'e2b.example.com'
TEMPLATE = 'openhands-agent-server'
INIT_API_KEY = 'template-init-key'
OWNER_ID = 'user-1'
OTHER_USER_ID = 'user-2'
SANDBOX_ID = 'ixyz123'
WEB_URL = 'https://app.example.com'
AGENT_SERVER_URL = f'https://8000-{SANDBOX_ID}.{DOMAIN}'
VSCODE_URL = (
    f'https://8001-{SANDBOX_ID}.{DOMAIN}/?tkn=deadbeef&folder=/workspace/project'
)
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
SESSION_API_KEY = 'the-session-key'


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _e2b_info(
    sandbox_id: str = SANDBOX_ID,
    state: SandboxState = SandboxState.RUNNING,
    user_id: str | None = OWNER_ID,
    spec_id: str = TEMPLATE,
    managed: bool = True,
) -> SimpleNamespace:
    """The subset of E2B's SandboxInfo the service reads."""
    metadata = {SANDBOX_SPEC_ID_METADATA_KEY: spec_id}
    if managed:
        metadata[MANAGED_METADATA_KEY] = 'true'
    if user_id:
        metadata[CREATED_BY_USER_ID_METADATA_KEY] = user_id
    return SimpleNamespace(
        sandbox_id=sandbox_id,
        state=state,
        metadata=metadata,
        started_at=CREATED_AT,
    )


def _paginator(items: list, next_token: str | None = None) -> MagicMock:
    """Stand in for e2b's paginator, including when it says it is done.

    ``has_next`` starts True and goes False once the single page has been
    handed out, which is the contract the SDK documents and the one the
    service's drain loop terminates on.
    """
    paginator = MagicMock()
    paginator.has_next = True

    async def _next_items():
        paginator.has_next = False
        return items

    paginator.next_items = AsyncMock(side_effect=_next_items)
    paginator.next_token = next_token
    return paginator


def _mock_sdk() -> MagicMock:
    """Stand in for e2b.AsyncSandbox. `list` is sync, everything else is not."""
    sdk = MagicMock()
    sdk.create = AsyncMock(return_value=SimpleNamespace(sandbox_id=SANDBOX_ID))
    sdk.get_info = AsyncMock(return_value=_e2b_info())
    sdk.connect = AsyncMock()
    sdk.pause = AsyncMock(return_value=True)
    sdk.kill = AsyncMock(return_value=True)
    sdk.list = MagicMock(return_value=_paginator([]))
    return sdk


def _response(status_code: int = 200, payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = str(payload)
    response.json.return_value = payload if payload is not None else {}
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'error', request=MagicMock(), response=MagicMock()
        )
    return response


class FakeAgentServer:
    """httpx.AsyncClient stand in that answers the agent server routes."""

    def __init__(
        self,
        init_get_responses: list | None = None,
        init_post_status: int = 200,
        vscode_url: str | None = VSCODE_URL,
    ):
        self.init_get_responses = init_get_responses or []
        self.init_post_status = init_post_status
        self.vscode_url = vscode_url
        self.init_post_bodies: list[dict] = []
        self.init_post_headers: list[dict] = []
        self.vscode_requests: list[dict] = []
        self.init_get_count = 0

    async def get(self, url: str, **kwargs):
        if url.endswith('/api/init'):
            self.init_get_count += 1
            if self.init_get_responses:
                result = self.init_get_responses.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return _response(200, {'state': 'dormant', 'error': None})
        if url.endswith('/api/vscode/url'):
            self.vscode_requests.append(
                {'url': url, 'params': kwargs.get('params'), **kwargs}
            )
            if self.vscode_url is None:
                raise httpx.ConnectError('no route to sandbox')
            return _response(200, {'url': self.vscode_url})
        raise AssertionError(f'unexpected GET {url}')

    async def post(self, url: str, **kwargs):
        assert url.endswith('/api/init'), f'unexpected POST {url}'
        self.init_post_bodies.append(kwargs['json'])
        self.init_post_headers.append(kwargs.get('headers') or {})
        return _response(self.init_post_status, {'state': 'ready'})


def _stored(
    sandbox_id: str = SANDBOX_ID,
    created_by_user_id: str | None = OWNER_ID,
    sandbox_spec_id: str = TEMPLATE,
    session_api_key: str | None = SESSION_API_KEY,
    created_at: datetime | None = None,
) -> StoredSandbox:
    """The sandbox table row start_sandbox writes for an E2B sandbox."""
    return StoredSandbox(
        id=sandbox_id,
        backend=E2B_BACKEND,
        created_by_user_id=created_by_user_id,
        sandbox_spec_id=sandbox_spec_id,
        session_api_key_hash=(
            hash_session_api_key(session_api_key) if session_api_key else None
        ),
        session_api_key=SecretStr(session_api_key) if session_api_key else None,
        created_at=created_at or CREATED_AT,
    )


async def _row_exists(db_session, sandbox_id: str) -> bool:
    """Whether the sandbox's row is still in the database."""
    await db_session.flush()
    result = await db_session.execute(
        select(StoredSandbox.id).where(StoredSandbox.id == sandbox_id)
    )
    return result.scalar_one_or_none() is not None


def _user_context(user_id: str | None) -> AsyncMock:
    context = AsyncMock()
    context.get_user_id.return_value = user_id
    context.get_default_sandbox_spec_id.return_value = None
    return context


def _service(
    db_session,
    user_id: str | None = OWNER_ID,
    user_context: UserContext | None = None,
    httpx_client=None,
    web_url: str | None = WEB_URL,
    permitted_cors_origins: list[str] | None = None,
    init_api_key: str | None = INIT_API_KEY,
    init_timeout_seconds: int = 5,
    resume_retries: int = 3,
) -> E2BSandboxService:
    spec = E2BSandboxSpecInfo(
        id=TEMPLATE,
        command=None,
        working_dir='/workspace/project',
        init_api_key=SecretStr(init_api_key) if init_api_key else None,
    )
    return E2BSandboxService(
        sandbox_spec_service=PresetSandboxSpecService(specs=[spec]),
        user_context=user_context or _user_context(user_id),
        httpx_client=httpx_client or FakeAgentServer(),
        db_session=db_session,
        api_key='e2b-api-key',
        domain=DOMAIN,
        timeout_seconds=3600,
        max_num_sandboxes=10,
        init_timeout_seconds=init_timeout_seconds,
        init_poll_interval=0,
        resume_retries=resume_retries,
        resume_retry_interval=0,
        api_url='https://api.e2b.example.com',
        web_url=web_url,
        permitted_cors_origins=permitted_cors_origins or [],
    )


@pytest.fixture(autouse=True)
def clear_vscode_cache():
    e2b_sandbox_service._vscode_urls.clear()
    yield
    e2b_sandbox_service._vscode_urls.clear()


@pytest.fixture
async def db_session(async_session_maker):
    """A session on this test's own postgres database."""
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def service(db_session):
    """Service acting for the sandbox owner."""
    return _service(db_session)


@pytest.fixture
def store(db_session):
    """Add the rows standing for the sandboxes a test sets up."""

    async def _store(*sandboxes: StoredSandbox) -> None:
        for sandbox in sandboxes:
            db_session.add(sandbox)
        await db_session.flush()

    return _store


@pytest.fixture
def sdk():
    mock = _mock_sdk()
    with patch.object(e2b_sandbox_service, 'AsyncSandbox', mock):
        yield mock


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestStartSandbox:
    @pytest.mark.asyncio
    async def test_metadata_records_owner_and_spec(self, sdk, db_session):
        sandbox = await _service(db_session).start_sandbox()

        assert sdk.create.await_args.kwargs['metadata'] == {
            MANAGED_METADATA_KEY: 'true',
            SANDBOX_SPEC_ID_METADATA_KEY: TEMPLATE,
            CREATED_BY_USER_ID_METADATA_KEY: OWNER_ID,
        }
        assert sandbox.id == SANDBOX_ID
        assert sandbox.created_by_user_id == OWNER_ID
        assert sandbox.sandbox_spec_id == TEMPLATE
        assert sandbox.status == SandboxStatus.RUNNING
        assert sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_needs_an_owner(self, sdk, db_session):
        """ADMIN has no user id, so it must not start a sandbox or pause any."""
        service = _service(db_session, user_context=ADMIN)

        with (
            patch.object(service, 'pause_old_sandboxes') as pause,
            pytest.raises(AuthError),
        ):
            await service.start_sandbox()

        pause.assert_not_called()
        sdk.create.assert_not_called()
        assert (await db_session.execute(select(StoredSandbox))).first() is None

    @pytest.mark.asyncio
    async def test_created_with_pause_on_timeout(self, sdk, db_session):
        await _service(db_session).start_sandbox()

        kwargs = sdk.create.await_args.kwargs
        assert kwargs['template'] == TEMPLATE
        assert kwargs['timeout'] == 3600
        assert kwargs['lifecycle'] == {'on_timeout': 'pause', 'auto_resume': True}

    @pytest.mark.asyncio
    async def test_connection_options_passed_explicitly(self, sdk, db_session):
        await _service(db_session).start_sandbox()

        kwargs = sdk.create.await_args.kwargs
        assert kwargs['api_key'] == 'e2b-api-key'
        assert kwargs['domain'] == DOMAIN
        assert kwargs['api_url'] == 'https://api.e2b.example.com'

    @pytest.mark.asyncio
    async def test_sandbox_id_hint_is_ignored(self, sdk, db_session):
        """E2B assigns the id; SandboxInfo.id is the E2B sandbox id."""
        sandbox = await _service(db_session).start_sandbox(
            sandbox_id='caller-chosen-id'
        )

        assert sandbox.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_create_failure_raises_sandbox_error(self, sdk, db_session):
        sdk.create.side_effect = SandboxException('boom')

        with pytest.raises(SandboxError) as raised:
            await _service(db_session).start_sandbox()

        assert 'Failed to start sandbox' in str(raised.value.detail)

    @pytest.mark.asyncio
    async def test_a_placement_failure_names_the_template_size(self, sdk, db_session):
        """A template too big for any node fails permanently, not transiently."""
        sdk.create.side_effect = SandboxException(
            '500: Failed to place sandbox: sandbox creation failed on 1 '
            'node(s), please retry; if the problem persists, contact us'
        )

        with pytest.raises(SandboxError) as raised:
            await _service(db_session).start_sandbox()

        detail = str(raised.value.detail)
        assert 'could not place a sandbox' in detail
        assert TEMPLATE in detail
        assert 'memory' in detail
        # Retrying does not recover a template that does not fit.
        assert sdk.create.await_count == 1


class TestInitHandshake:
    @pytest.mark.asyncio
    async def test_init_body_is_accepted_by_the_agent_server(self, sdk, db_session):
        agent_server = FakeAgentServer()

        sandbox = await _service(db_session, httpx_client=agent_server).start_sandbox()

        body = agent_server.init_post_bodies[0]
        # extra='forbid' on the real model: validating proves the body carries
        # only fields the agent server accepts.
        InitRequest.model_validate(body)
        assert set(body) == {
            'session_api_keys',
            'secret_key',
            'conversations_path',
            'bash_events_dir',
            'conversation_worktree_root',
            'allow_cors_origins',
            'webhooks',
            'env',
        }
        assert body['session_api_keys'] == [sandbox.session_api_key]
        assert body['conversations_path'] == '/workspace/conversations'
        assert body['bash_events_dir'] == '/workspace/bash_events'
        assert body['conversation_worktree_root'] == '/workspace/worktrees'
        assert body['webhooks'] == [{'base_url': f'{WEB_URL}/api/v1/webhooks'}]
        assert body['allow_cors_origins'] == [WEB_URL]
        assert agent_server.init_post_headers[0] == {'X-Init-API-Key': INIT_API_KEY}

    @pytest.mark.asyncio
    async def test_secret_key_is_rotated_per_sandbox(self, sdk, db_session):
        agent_server = FakeAgentServer()
        service = _service(db_session, httpx_client=agent_server)

        await service.start_sandbox()
        sdk.create.return_value = SimpleNamespace(sandbox_id='iother456')
        await service.start_sandbox()

        first, second = (body['secret_key'] for body in agent_server.init_post_bodies)
        assert first != second
        assert INIT_API_KEY not in (first, second)

    @pytest.mark.asyncio
    async def test_env_carries_worker_ports_and_agent_server_env(
        self,
        sdk,
        monkeypatch,
        db_session,
    ):
        monkeypatch.setenv('LLM_API_KEY', 'sk-secret')
        monkeypatch.setenv('LLM_TIMEOUT', '3600')
        agent_server = FakeAgentServer()

        await _service(db_session, httpx_client=agent_server).start_sandbox()

        env = agent_server.init_post_bodies[0]['env']
        assert env[WORKER_1] == str(WORKER_1_PORT)
        assert env[WORKER_2] == str(WORKER_2_PORT)
        assert env['LLM_API_KEY'] == 'sk-secret'
        assert env['LLM_TIMEOUT'] == '3600'

    @pytest.mark.asyncio
    async def test_cors_origins_include_permitted_origins(self, sdk, db_session):
        agent_server = FakeAgentServer()

        await _service(
            db_session,
            httpx_client=agent_server,
            permitted_cors_origins=['https://other.example.com', WEB_URL],
        ).start_sandbox()

        assert agent_server.init_post_bodies[0]['allow_cors_origins'] == [
            WEB_URL,
            'https://other.example.com',
        ]

    @pytest.mark.asyncio
    async def test_no_webhook_without_a_public_web_url(self, sdk, db_session):
        agent_server = FakeAgentServer()

        with patch.object(e2b_sandbox_service._logger, 'warning') as warning:
            await _service(
                db_session, httpx_client=agent_server, web_url='http://localhost:3000'
            ).start_sandbox()

        assert 'webhooks' not in agent_server.init_post_bodies[0]
        assert 'OH_WEB_URL' in warning.call_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize('init_api_key', [None, ''])
    async def test_a_missing_init_key_fails_before_creating_a_sandbox(
        self,
        sdk,
        init_api_key,
        db_session,
    ):
        agent_server = FakeAgentServer()

        with pytest.raises(SandboxError) as raised:
            await _service(
                db_session, httpx_client=agent_server, init_api_key=init_api_key
            ).start_sandbox()

        sdk.create.assert_not_awaited()
        sdk.kill.assert_not_awaited()
        assert not agent_server.init_post_bodies
        assert 'E2B_INIT_API_KEY' in str(raised.value.detail)
        assert 'OH_SECRET_KEY' in str(raised.value.detail)

    @pytest.mark.asyncio
    async def test_a_rejected_init_key_says_so(self, sdk, db_session):
        agent_server = FakeAgentServer(init_post_status=401)

        with pytest.raises(SandboxError) as raised:
            await _service(db_session, httpx_client=agent_server).start_sandbox()

        detail = str(raised.value.detail)
        assert 'init API key' in detail
        assert 'OH_SECRET_KEY' in detail
        assert INIT_API_KEY not in detail

    @pytest.mark.asyncio
    async def test_other_init_failures_keep_the_generic_message(self, sdk, db_session):
        agent_server = FakeAgentServer(init_post_status=500)

        with pytest.raises(SandboxError) as raised:
            await _service(db_session, httpx_client=agent_server).start_sandbox()

        assert 'Failed to initialize sandbox' in str(raised.value.detail)

    @pytest.mark.asyncio
    async def test_waits_for_dormant_through_edge_errors(self, sdk, db_session):
        agent_server = FakeAgentServer(
            init_get_responses=[
                _response(
                    502, {'message': 'The sandbox is running but port is not open'}
                ),
                httpx.ConnectError('connection reset'),
                _response(200, {'state': 'dormant', 'error': None}),
            ]
        )

        await _service(db_session, httpx_client=agent_server).start_sandbox()

        assert agent_server.init_get_count == 3
        assert len(agent_server.init_post_bodies) == 1

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_init_never_becomes_dormant(
        self, sdk, db_session
    ):
        agent_server = FakeAgentServer(
            init_get_responses=[_response(502, {'message': 'nope'})]
        )

        with pytest.raises(SandboxError):
            await _service(
                db_session, httpx_client=agent_server, init_timeout_seconds=0
            ).start_sandbox()

        sdk.kill.assert_awaited_once()
        assert sdk.kill.await_args.args[0] == SANDBOX_ID
        assert not agent_server.init_post_bodies

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_init_is_rejected(self, sdk, db_session):
        agent_server = FakeAgentServer(init_post_status=403)

        with pytest.raises(SandboxError):
            await _service(db_session, httpx_client=agent_server).start_sandbox()

        sdk.kill.assert_awaited_once()
        # Init is never retried: a replay cannot be told apart from a bad key.
        assert len(agent_server.init_post_bodies) == 1

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_already_initialized(self, sdk, db_session):
        agent_server = FakeAgentServer(
            init_get_responses=[_response(200, {'state': 'ready'})]
        )

        with pytest.raises(SandboxError):
            await _service(db_session, httpx_client=agent_server).start_sandbox()

        sdk.kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_its_row_cannot_be_written(
        self, sdk, db_session
    ):
        """E2B assigns the id, so the sandbox exists before its row does."""
        with (
            patch.object(
                db_session, 'flush', AsyncMock(side_effect=RuntimeError('db down'))
            ),
            pytest.raises(RuntimeError, match='db down'),
        ):
            await _service(db_session).start_sandbox()

        sdk.kill.assert_awaited_once()


# ---------------------------------------------------------------------------
# Session API keys
# ---------------------------------------------------------------------------


class TestSessionApiKeys:
    """The key is minted per sandbox and kept on the row, encrypted."""

    @pytest.mark.asyncio
    async def test_round_trip(self, sdk, db_session):
        service = _service(db_session)
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key

        found = await service.get_sandbox_by_session_api_key(sandbox.session_api_key)

        assert found is not None
        assert found.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_key_is_not_derived_from_the_sandbox_id(self, sdk, db_session):
        """A key that carries its own sandbox id hands out a free oracle."""
        sandbox = await _service(db_session).start_sandbox()

        assert sandbox.session_api_key
        assert SANDBOX_ID not in sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_each_sandbox_gets_its_own_key(self, sdk, db_session):
        service = _service(db_session)
        first = await service.start_sandbox()

        sdk.create.return_value = SimpleNamespace(sandbox_id='iother456')
        sdk.get_info.return_value = _e2b_info(sandbox_id='iother456')
        second = await service.start_sandbox()

        assert first.session_api_key != second.session_api_key

    @pytest.mark.asyncio
    async def test_the_key_is_stored_encrypted(self, sdk, db_session):
        """The column holds ciphertext; the ORM hands back the key."""
        sandbox = await _service(db_session).start_sandbox()
        assert sandbox.session_api_key

        row = (
            await db_session.execute(
                select(StoredSandbox).where(StoredSandbox.id == SANDBOX_ID)
            )
        ).scalar_one()
        assert row.session_api_key is not None
        assert row.session_api_key.get_secret_value() == sandbox.session_api_key

        stored = (
            await db_session.execute(
                text('SELECT session_api_key FROM v1_remote_sandbox WHERE id = :id'),
                {'id': SANDBOX_ID},
            )
        ).scalar_one()
        assert stored != sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_a_wrong_key_is_rejected_without_an_e2b_call(self, sdk, db_session):
        service = _service(db_session)
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key
        sdk.get_info.reset_mock()
        tampered = sandbox.session_api_key[:-1] + (
            'A' if sandbox.session_api_key[-1] != 'A' else 'B'
        )

        assert await service.get_sandbox_by_session_api_key(tampered) is None
        assert await service.get_sandbox_record_by_session_api_key(tampered) is None
        sdk.get_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_key_is_rejected(self, sdk, db_session):
        service = _service(db_session)

        assert await service.get_sandbox_by_session_api_key('no-separator') is None
        assert await service.get_sandbox_by_session_api_key('') is None
        assert await service.get_sandbox_by_session_api_key('.digest') is None

    @pytest.mark.asyncio
    async def test_record_lookup_makes_no_e2b_call(self, sdk, db_session):
        """The webhook path runs once per event batch, so it must not call E2B."""
        service = _service(db_session)
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key
        sdk.get_info.reset_mock()

        record = await service.get_sandbox_record_by_session_api_key(
            sandbox.session_api_key
        )

        assert record is not None
        assert record.id == SANDBOX_ID
        assert record.created_by_user_id == OWNER_ID
        sdk.get_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_key_survives_pause_and_resume(self, sdk, db_session):
        """Same id, same host, process restored from the snapshot."""
        service = _service(db_session)
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key

        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        assert await service.pause_sandbox(SANDBOX_ID) is True
        assert await service.resume_sandbox(SANDBOX_ID) is True
        sdk.get_info.return_value = _e2b_info(state=SandboxState.RUNNING)

        resumed = await service.get_sandbox(SANDBOX_ID)
        assert resumed is not None
        assert resumed.session_api_key == sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_delete_revokes_the_key(self, sdk, db_session):
        service = _service(db_session)
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key

        assert await service.delete_sandbox(SANDBOX_ID) is True

        assert (
            await service.get_sandbox_by_session_api_key(sandbox.session_api_key)
        ) is None
        assert (
            await service.get_sandbox_record_by_session_api_key(sandbox.session_api_key)
        ) is None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class TestGetSandbox:
    @pytest.fixture(autouse=True)
    async def existing_sandbox(self, store):
        """The row start_sandbox would have written for SANDBOX_ID."""
        await store(_stored())

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'state,expected',
        [
            (SandboxState.RUNNING, SandboxStatus.RUNNING),
            (SandboxState.PAUSED, SandboxStatus.PAUSED),
        ],
    )
    async def test_status_mapping(self, sdk, state, expected, db_session):
        sdk.get_info.return_value = _e2b_info(state=state)

        sandbox = await _service(db_session).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.status == expected
        assert sandbox.created_at == CREATED_AT

    @pytest.mark.asyncio
    async def test_paused_sandbox_exposes_no_urls_or_key(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)

        sandbox = await _service(db_session).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.session_api_key is None
        assert sandbox.exposed_urls is None

    @pytest.mark.asyncio
    async def test_a_sandbox_e2b_has_reaped_is_missing(self, sdk, db_session):
        """E2B's SandboxState has no reaped state, so the row supplies it."""
        sdk.get_info.side_effect = SandboxNotFoundException('Sandbox not found')

        sandbox = await _service(db_session).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.status == SandboxStatus.MISSING
        assert sandbox.created_by_user_id == OWNER_ID
        assert sandbox.session_api_key is None
        assert sandbox.exposed_urls is None

    @pytest.mark.asyncio
    async def test_malformed_id_returns_none(self, sdk, db_session):
        sdk.get_info.side_effect = SandboxException('400: Invalid sandbox ID')

        assert await _service(db_session).get_sandbox('not-an-id') is None

    @pytest.mark.asyncio
    async def test_a_deleted_sandbox_is_invisible(self, sdk, db_session, store):
        await store(_stored(sandbox_id='igone', session_api_key=None))
        service = _service(db_session)
        assert await service.delete_sandbox('igone') is True

        assert await service.get_sandbox('igone') is None

    @pytest.mark.asyncio
    async def test_a_sandbox_with_no_row_is_invisible(self, sdk, db_session):
        """A sandbox the app has no record of is not the app's to hand out."""
        sdk.get_info.return_value = _e2b_info(sandbox_id='iunknown', managed=False)

        assert (
            await _service(db_session, user_context=ADMIN).get_sandbox('iunknown')
            is None
        )


class TestExposedUrls:
    @pytest.fixture(autouse=True)
    async def existing_sandbox(self, store):
        """The row start_sandbox would have written for SANDBOX_ID."""
        await store(_stored())

    @pytest.mark.asyncio
    async def test_urls_are_built_from_id_and_domain(self, sdk, db_session):
        sandbox = await _service(db_session).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.exposed_urls is not None
        urls = {url.name: (url.url, url.port) for url in sandbox.exposed_urls}
        assert urls[AGENT_SERVER] == (AGENT_SERVER_URL, 8000)
        assert urls[WORKER_1] == (
            f'https://{WORKER_1_PORT}-{SANDBOX_ID}.{DOMAIN}',
            WORKER_1_PORT,
        )
        assert urls[WORKER_2] == (
            f'https://{WORKER_2_PORT}-{SANDBOX_ID}.{DOMAIN}',
            WORKER_2_PORT,
        )

    @pytest.mark.asyncio
    async def test_vscode_url_comes_from_the_agent_server(self, sdk, db_session):
        agent_server = FakeAgentServer()

        sandbox = await _service(db_session, httpx_client=agent_server).get_sandbox(
            SANDBOX_ID
        )

        assert sandbox is not None
        assert sandbox.exposed_urls is not None
        vscode = next(u for u in sandbox.exposed_urls if u.name == VSCODE)
        assert vscode.url == VSCODE_URL
        assert vscode.port == 8001
        assert agent_server.vscode_requests[0]['params'] == {
            'base_url': f'https://8001-{SANDBOX_ID}.{DOMAIN}',
            'workspace_dir': '/workspace/project',
        }
        assert agent_server.vscode_requests[0]['headers'] == {
            'X-Session-API-Key': sandbox.session_api_key
        }

    @pytest.mark.asyncio
    async def test_vscode_url_is_cached(self, sdk, db_session):
        agent_server = FakeAgentServer()
        service = _service(db_session, httpx_client=agent_server)

        await service.get_sandbox(SANDBOX_ID)
        await service.get_sandbox(SANDBOX_ID)
        await service.get_sandbox(SANDBOX_ID)

        assert len(agent_server.vscode_requests) == 1

    @pytest.mark.asyncio
    async def test_vscode_url_is_populated_by_start_and_reused(self, sdk, db_session):
        agent_server = FakeAgentServer()
        service = _service(db_session, httpx_client=agent_server)
        sdk.create.return_value = SimpleNamespace(sandbox_id='ifresh')
        sdk.get_info.return_value = _e2b_info(sandbox_id='ifresh')

        await service.start_sandbox()
        await service.get_sandbox('ifresh')

        assert len(agent_server.vscode_requests) == 1

    @pytest.mark.asyncio
    async def test_vscode_failure_omits_the_url(self, sdk, db_session):
        agent_server = FakeAgentServer(vscode_url=None)

        sandbox = await _service(db_session, httpx_client=agent_server).get_sandbox(
            SANDBOX_ID
        )

        assert sandbox is not None
        assert sandbox.exposed_urls is not None
        assert all(url.name != VSCODE for url in sandbox.exposed_urls)
        assert {url.name for url in sandbox.exposed_urls} == {
            AGENT_SERVER,
            WORKER_1,
            WORKER_2,
        }

    @pytest.mark.asyncio
    async def test_cache_is_bounded(self, sdk):
        for index in range(e2b_sandbox_service.VSCODE_URL_CACHE_SIZE + 5):
            e2b_sandbox_service._cache_vscode_url(f'sbx-{index}', 'https://vscode')

        assert (
            len(e2b_sandbox_service._vscode_urls)
            == e2b_sandbox_service.VSCODE_URL_CACHE_SIZE
        )
        assert 'sbx-0' not in e2b_sandbox_service._vscode_urls
        assert 'sbx-4' not in e2b_sandbox_service._vscode_urls
        assert 'sbx-5' in e2b_sandbox_service._vscode_urls

    @pytest.mark.asyncio
    async def test_delete_evicts_the_cached_url(self, sdk, db_session):
        agent_server = FakeAgentServer()
        service = _service(db_session, httpx_client=agent_server)
        await service.get_sandbox(SANDBOX_ID)

        await service.delete_sandbox(SANDBOX_ID)

        assert SANDBOX_ID not in e2b_sandbox_service._vscode_urls


class TestSearchSandboxes:
    """One DB query for the page, one E2B call for the live state on it."""

    @pytest.mark.asyncio
    async def test_returns_the_callers_sandboxes(self, sdk, db_session, store):
        await store(_stored())
        sdk.list.return_value = _paginator([_e2b_info()])

        page = await _service(db_session).search_sandboxes()

        assert [item.id for item in page.items] == [SANDBOX_ID]
        assert page.items[0].created_by_user_id == OWNER_ID
        assert page.items[0].sandbox_spec_id == TEMPLATE
        assert page.items[0].status == SandboxStatus.RUNNING

    @pytest.mark.asyncio
    async def test_another_users_sandbox_is_not_returned(self, sdk, db_session, store):
        await store(_stored(created_by_user_id=OTHER_USER_ID))
        sdk.list.return_value = _paginator([_e2b_info(user_id=OTHER_USER_ID)])

        page = await _service(db_session).search_sandboxes()

        assert page.items == []

    @pytest.mark.asyncio
    async def test_admin_sees_every_managed_sandbox(self, sdk, db_session, store):
        await store(_stored(created_by_user_id=OTHER_USER_ID))
        sdk.list.return_value = _paginator([_e2b_info(user_id=OTHER_USER_ID)])

        page = await _service(db_session, user_context=ADMIN).search_sandboxes()

        assert [item.id for item in page.items] == [SANDBOX_ID]
        query = sdk.list.call_args.kwargs['query']
        assert query.metadata == {MANAGED_METADATA_KEY: 'true'}

    @pytest.mark.asyncio
    async def test_asks_e2b_only_for_the_callers_sandboxes(
        self, sdk, db_session, store
    ):
        """The owner filter narrows the list, so a MISSING row cannot scan the team."""
        await store(_stored())
        sdk.list.return_value = _paginator([_e2b_info()])

        await _service(db_session).search_sandboxes()

        query = sdk.list.call_args.kwargs['query']
        assert query.metadata == {
            MANAGED_METADATA_KEY: 'true',
            CREATED_BY_USER_ID_METADATA_KEY: OWNER_ID,
        }
        assert set(query.state) == {SandboxState.RUNNING, SandboxState.PAUSED}
        assert sdk.list.call_count == 1

    @pytest.mark.asyncio
    async def test_a_row_e2b_does_not_know_about_is_missing(
        self, sdk, db_session, store
    ):
        await store(_stored())
        sdk.list.return_value = _paginator([])

        page = await _service(db_session).search_sandboxes()

        assert [item.status for item in page.items] == [SandboxStatus.MISSING]

    @pytest.mark.asyncio
    async def test_pages_on_an_offset(self, sdk, db_session, store):
        await store(
            *[
                _stored(
                    sandbox_id=f'sb-{index}',
                    created_at=CREATED_AT + timedelta(days=index),
                )
                for index in range(3)
            ]
        )
        sdk.list.side_effect = lambda **kwargs: _paginator([])
        service = _service(db_session)

        first = await service.search_sandboxes(limit=2)
        assert [item.id for item in first.items] == ['sb-2', 'sb-1']
        assert first.next_page_id == '2'

        second = await service.search_sandboxes(page_id=first.next_page_id, limit=2)
        assert [item.id for item in second.items] == ['sb-0']
        assert second.next_page_id is None

    @pytest.mark.asyncio
    async def test_list_failure_is_reported_rather_than_read_as_empty(
        self, sdk, db_session, store
    ):
        # An empty page would tell `pause_old_sandboxes` the cap is not
        # reached and tell the conversation-start lookup to provision another
        # sandbox, so a rate limit would produce more load rather than less.
        await store(_stored())
        paginator = _paginator([])
        paginator.next_items.side_effect = SandboxException('boom')
        sdk.list.return_value = paginator

        with pytest.raises(SandboxError, match='Could not list sandboxes'):
            await _service(db_session).search_sandboxes()

    @pytest.mark.asyncio
    async def test_no_vscode_call_per_sandbox(self, sdk, db_session, store):
        await store(
            _stored(sandbox_id='sb-1', created_at=CREATED_AT),
            _stored(sandbox_id='sb-2', created_at=CREATED_AT - timedelta(days=1)),
        )
        agent_server = FakeAgentServer()
        sdk.list.return_value = _paginator(
            [_e2b_info(sandbox_id='sb-1'), _e2b_info(sandbox_id='sb-2')]
        )

        page = await _service(db_session, httpx_client=agent_server).search_sandboxes()

        assert agent_server.vscode_requests == []
        assert [item.id for item in page.items] == ['sb-1', 'sb-2']
        for item in page.items:
            assert item.exposed_urls is not None
            assert {url.name for url in item.exposed_urls} == {
                AGENT_SERVER,
                WORKER_1,
                WORKER_2,
            }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.fixture(autouse=True)
    async def existing_sandbox(self, store):
        """The row start_sandbox would have written for SANDBOX_ID."""
        await store(_stored())

    @pytest.mark.asyncio
    async def test_pause_uses_the_sdk(self, sdk, db_session):
        assert await _service(db_session).pause_sandbox(SANDBOX_ID) is True

        sdk.pause.assert_awaited_once()
        assert sdk.pause.await_args.args[0] == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_pause_of_a_paused_sandbox_is_a_no_op(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)

        assert await _service(db_session).pause_sandbox(SANDBOX_ID) is True

        sdk.pause.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_of_a_missing_sandbox_returns_false(self, sdk, db_session):
        sdk.get_info.side_effect = SandboxNotFoundException('gone')

        assert await _service(db_session).pause_sandbox(SANDBOX_ID) is False

    @pytest.mark.asyncio
    async def test_resume_connects_with_a_fresh_timeout(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)

        assert await _service(db_session).resume_sandbox(SANDBOX_ID) is True

        sdk.connect.assert_awaited_once()
        assert sdk.connect.await_args.args[0] == SANDBOX_ID
        assert sdk.connect.await_args.kwargs['timeout'] == 3600

    @pytest.mark.asyncio
    async def test_resume_without_a_row_returns_false(self, sdk, db_session):
        assert await _service(db_session).resume_sandbox('iunknown') is False
        sdk.connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_of_a_sandbox_e2b_lost_returns_false(self, sdk, db_session):
        sdk.connect.side_effect = SandboxNotFoundException('gone')

        assert await _service(db_session).resume_sandbox(SANDBOX_ID) is False

    @pytest.mark.asyncio
    async def test_resume_retries_while_the_snapshot_settles(self, sdk, db_session):
        """A sandbox reports paused before its snapshot can be placed."""
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = [
            SandboxException('500: Failed to place sandbox'),
            None,
        ]

        assert await _service(db_session).resume_sandbox(SANDBOX_ID) is True

        assert sdk.connect.await_count == 2

    @pytest.mark.asyncio
    async def test_resume_gives_up_after_the_retry_budget(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = SandboxException('500: Failed to place sandbox')

        assert (
            await _service(db_session, resume_retries=3).resume_sandbox(SANDBOX_ID)
            is False
        )

        assert sdk.connect.await_count == 3

    @pytest.mark.asyncio
    async def test_resume_does_not_retry_a_vanished_sandbox(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = SandboxNotFoundException('Paused sandbox not found')

        assert await _service(db_session).resume_sandbox(SANDBOX_ID) is False

        assert sdk.connect.await_count == 1

    @pytest.mark.asyncio
    async def test_delete_kills_the_sandbox_and_removes_its_row(self, sdk, db_session):
        assert await _service(db_session).delete_sandbox(SANDBOX_ID) is True

        sdk.kill.assert_awaited_once()
        assert sdk.kill.await_args.args[0] == SANDBOX_ID
        assert not await _row_exists(db_session, SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_delete_without_a_row_returns_false(self, sdk, db_session):
        assert await _service(db_session).delete_sandbox('iunknown') is False
        sdk.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_removes_a_row_e2b_has_already_reaped(self, sdk, db_session):
        """Nothing left to kill is not a reason to ask for a retry."""
        sdk.kill.side_effect = SandboxNotFoundException('gone')
        service = _service(db_session)

        assert await service.delete_sandbox(SANDBOX_ID) is True
        assert await service.get_sandbox(SANDBOX_ID) is None
        assert not await _row_exists(db_session, SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_delete_of_an_already_dead_sandbox_succeeds(self, sdk, db_session):
        """kill() reports False when there was nothing to kill; not an error."""
        sdk.kill.return_value = False

        assert await _service(db_session).delete_sandbox(SANDBOX_ID) is True

    @pytest.mark.asyncio
    async def test_delete_failure_is_retryable(self, sdk, db_session):
        sdk.kill.side_effect = SandboxException('service unavailable')

        with pytest.raises(SandboxDeleteRetryError):
            await _service(db_session).delete_sandbox(SANDBOX_ID)

        assert await _row_exists(db_session, SANDBOX_ID)


# ---------------------------------------------------------------------------
# User scoping
# ---------------------------------------------------------------------------


class TestUserScoping:
    """Ownership comes off the row, so E2B metadata cannot be used to spoof it."""

    @pytest.fixture(autouse=True)
    async def owners_sandbox(self, store):
        await store(_stored(created_by_user_id=OWNER_ID))

    @pytest.fixture
    def intruder(self, sdk, db_session) -> E2BSandboxService:
        sdk.get_info.return_value = _e2b_info(user_id=OWNER_ID)
        return _service(db_session, user_id=OTHER_USER_ID)

    @pytest.mark.asyncio
    async def test_cannot_get(self, intruder):
        assert await intruder.get_sandbox(SANDBOX_ID) is None

    @pytest.mark.asyncio
    async def test_cannot_pause(self, intruder, sdk):
        assert await intruder.pause_sandbox(SANDBOX_ID) is False
        sdk.pause.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cannot_resume(self, intruder, sdk):
        assert await intruder.resume_sandbox(SANDBOX_ID) is False
        sdk.connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cannot_delete(self, intruder, sdk):
        assert await intruder.delete_sandbox(SANDBOX_ID) is False
        sdk.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cannot_search(self, intruder):
        assert (await intruder.search_sandboxes()).items == []

    @pytest.mark.asyncio
    async def test_cannot_resolve_a_leaked_session_key(self, intruder, db_session):
        owner = _service(db_session, user_id=OWNER_ID)
        sandbox = await owner.get_sandbox(SANDBOX_ID)
        assert sandbox is not None
        assert sandbox.session_api_key

        assert (
            await intruder.get_sandbox_by_session_api_key(sandbox.session_api_key)
        ) is None
        assert (
            await intruder.get_sandbox_record_by_session_api_key(
                sandbox.session_api_key
            )
        ) is None

    @pytest.mark.asyncio
    async def test_admin_can_get(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(user_id=OWNER_ID)

        sandbox = await _service(db_session, user_context=ADMIN).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.created_by_user_id == OWNER_ID

    @pytest.mark.asyncio
    async def test_e2b_metadata_cannot_override_the_row(self, sdk, db_session):
        """A sandbox whose metadata claims another owner still reads as its own."""
        sdk.get_info.return_value = _e2b_info(user_id=OTHER_USER_ID)

        sandbox = await _service(db_session, user_id=OWNER_ID).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.created_by_user_id == OWNER_ID


# ---------------------------------------------------------------------------
# Authentication failures
# ---------------------------------------------------------------------------


class TestAuthenticationFailures:
    """A rejected API key must not be reported as a missing sandbox.

    `AuthenticationException` is the one E2B error outside the
    `SandboxException` hierarchy, so without an explicit handler it escapes
    every call site untranslated.
    """

    @pytest.fixture(autouse=True)
    async def existing_sandbox(self, store):
        """The row start_sandbox would have written for SANDBOX_ID."""
        await store(_stored())

    @pytest.mark.asyncio
    async def test_get_names_the_api_key(self, sdk, db_session):
        sdk.get_info.side_effect = AuthenticationException('401 unauthorized')

        with pytest.raises(SandboxError, match='E2B_API_KEY'):
            await _service(db_session).get_sandbox(SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_search_names_the_api_key(self, sdk, db_session):
        paginator = _paginator([])
        paginator.next_items.side_effect = AuthenticationException('401 unauthorized')
        sdk.list.return_value = paginator

        with pytest.raises(SandboxError, match='E2B_API_KEY'):
            await _service(db_session).search_sandboxes()

    @pytest.mark.asyncio
    async def test_start_names_the_api_key(self, sdk, db_session):
        sdk.create.side_effect = AuthenticationException('401 unauthorized')

        with pytest.raises(SandboxError, match='E2B_API_KEY'):
            await _service(db_session).start_sandbox()

    @pytest.mark.asyncio
    async def test_pause_names_the_api_key(self, sdk, db_session):
        sdk.pause.side_effect = AuthenticationException('401 unauthorized')

        with pytest.raises(SandboxError, match='E2B_API_KEY'):
            await _service(db_session).pause_sandbox(SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_resume_names_the_api_key(self, sdk, db_session):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = AuthenticationException('401 unauthorized')

        with pytest.raises(SandboxError, match='E2B_API_KEY'):
            await _service(db_session).resume_sandbox(SANDBOX_ID)

    @pytest.mark.asyncio
    async def test_delete_names_the_api_key(self, sdk, db_session):
        sdk.kill.side_effect = AuthenticationException('401 unauthorized')

        with pytest.raises(SandboxError, match='E2B_API_KEY'):
            await _service(db_session).delete_sandbox(SANDBOX_ID)
