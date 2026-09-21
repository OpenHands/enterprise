"""Tests for E2BSandboxService.

The E2B SDK is mocked throughout; the agent server is replaced by a fake that
records the requests the service makes to it. Focus areas:
- sandbox metadata as the store for ownership and spec identity
- user scoping, including cross user isolation and the admin (no user id) case
- session API keys derived from the sandbox id rather than stored
- the /api/init handshake start_sandbox completes before returning
- lifecycle and status mapping onto the E2B SDK
- exposed URL construction and VSCode URL resolution
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from e2b import SandboxException, SandboxNotFoundException, SandboxState
from pydantic import SecretStr

from openhands.agent_server.init_router import InitRequest
from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
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
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey

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
    paginator = MagicMock()
    paginator.next_items = AsyncMock(return_value=items)
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


def _jwt_service(*secrets: str) -> JwtService:
    """JwtService whose newest (default) key is the last secret given."""
    return JwtService(
        keys=[
            EncryptionKey(
                id=f'key-{index}',
                key=SecretStr(secret),
                created_at=datetime(2020 + index, 1, 1, tzinfo=UTC),
            )
            for index, secret in enumerate(secrets)
        ]
    )


def _user_context(user_id: str | None) -> AsyncMock:
    context = AsyncMock()
    context.get_user_id.return_value = user_id
    context.get_default_sandbox_spec_id.return_value = None
    return context


def _service(
    user_id: str | None = OWNER_ID,
    httpx_client=None,
    jwt_service: JwtService | None = None,
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
        user_context=_user_context(user_id),
        httpx_client=httpx_client or FakeAgentServer(),
        jwt_service=jwt_service or _jwt_service('the-secret'),
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
def sdk():
    mock = _mock_sdk()
    with patch.object(e2b_sandbox_service, 'AsyncSandbox', mock):
        yield mock


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestStartSandbox:
    @pytest.mark.asyncio
    async def test_metadata_records_owner_and_spec(self, sdk):
        sandbox = await _service().start_sandbox()

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
    async def test_metadata_omits_owner_for_admin(self, sdk):
        sandbox = await _service(user_id=None).start_sandbox()

        assert (
            CREATED_BY_USER_ID_METADATA_KEY
            not in sdk.create.await_args.kwargs['metadata']
        )
        assert sandbox.created_by_user_id is None

    @pytest.mark.asyncio
    async def test_created_with_pause_on_timeout(self, sdk):
        await _service().start_sandbox()

        kwargs = sdk.create.await_args.kwargs
        assert kwargs['template'] == TEMPLATE
        assert kwargs['timeout'] == 3600
        assert kwargs['lifecycle'] == {'on_timeout': 'pause', 'auto_resume': True}

    @pytest.mark.asyncio
    async def test_connection_options_passed_explicitly(self, sdk):
        await _service().start_sandbox()

        kwargs = sdk.create.await_args.kwargs
        assert kwargs['api_key'] == 'e2b-api-key'
        assert kwargs['domain'] == DOMAIN
        assert kwargs['api_url'] == 'https://api.e2b.example.com'

    @pytest.mark.asyncio
    async def test_sandbox_id_hint_is_ignored(self, sdk):
        """E2B assigns the id; SandboxInfo.id is the E2B sandbox id."""
        sandbox = await _service().start_sandbox(sandbox_id='caller-chosen-id')

        assert sandbox.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_create_failure_raises_sandbox_error(self, sdk):
        sdk.create.side_effect = SandboxException('boom')

        with pytest.raises(SandboxError):
            await _service().start_sandbox()


class TestInitHandshake:
    @pytest.mark.asyncio
    async def test_init_body_is_accepted_by_the_agent_server(self, sdk):
        agent_server = FakeAgentServer()

        sandbox = await _service(httpx_client=agent_server).start_sandbox()

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
    async def test_secret_key_is_rotated_per_sandbox(self, sdk):
        agent_server = FakeAgentServer()
        service = _service(httpx_client=agent_server)

        await service.start_sandbox()
        await service.start_sandbox()

        first, second = (body['secret_key'] for body in agent_server.init_post_bodies)
        assert first != second
        assert INIT_API_KEY not in (first, second)

    @pytest.mark.asyncio
    async def test_env_carries_worker_ports_and_agent_server_env(
        self, sdk, monkeypatch
    ):
        monkeypatch.setenv('LLM_API_KEY', 'sk-secret')
        monkeypatch.setenv('LLM_TIMEOUT', '3600')
        agent_server = FakeAgentServer()

        await _service(httpx_client=agent_server).start_sandbox()

        env = agent_server.init_post_bodies[0]['env']
        assert env[WORKER_1] == str(WORKER_1_PORT)
        assert env[WORKER_2] == str(WORKER_2_PORT)
        assert env['LLM_API_KEY'] == 'sk-secret'
        assert env['LLM_TIMEOUT'] == '3600'

    @pytest.mark.asyncio
    async def test_cors_origins_include_permitted_origins(self, sdk):
        agent_server = FakeAgentServer()

        await _service(
            httpx_client=agent_server,
            permitted_cors_origins=['https://other.example.com', WEB_URL],
        ).start_sandbox()

        assert agent_server.init_post_bodies[0]['allow_cors_origins'] == [
            WEB_URL,
            'https://other.example.com',
        ]

    @pytest.mark.asyncio
    async def test_no_webhook_without_a_public_web_url(self, sdk):
        agent_server = FakeAgentServer()

        with patch.object(e2b_sandbox_service._logger, 'warning') as warning:
            await _service(
                httpx_client=agent_server, web_url='http://localhost:3000'
            ).start_sandbox()

        assert 'webhooks' not in agent_server.init_post_bodies[0]
        assert 'OH_WEB_URL' in warning.call_args.args[0]

    @pytest.mark.asyncio
    async def test_no_init_header_when_the_template_has_no_key(self, sdk):
        agent_server = FakeAgentServer()

        await _service(httpx_client=agent_server, init_api_key=None).start_sandbox()

        assert agent_server.init_post_headers[0] == {}

    @pytest.mark.asyncio
    async def test_waits_for_dormant_through_edge_errors(self, sdk):
        agent_server = FakeAgentServer(
            init_get_responses=[
                _response(
                    502, {'message': 'The sandbox is running but port is not open'}
                ),
                httpx.ConnectError('connection reset'),
                _response(200, {'state': 'dormant', 'error': None}),
            ]
        )

        await _service(httpx_client=agent_server).start_sandbox()

        assert agent_server.init_get_count == 3
        assert len(agent_server.init_post_bodies) == 1

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_init_never_becomes_dormant(self, sdk):
        agent_server = FakeAgentServer(
            init_get_responses=[_response(502, {'message': 'nope'})]
        )

        with pytest.raises(SandboxError):
            await _service(
                httpx_client=agent_server, init_timeout_seconds=0
            ).start_sandbox()

        sdk.kill.assert_awaited_once()
        assert sdk.kill.await_args.args[0] == SANDBOX_ID
        assert not agent_server.init_post_bodies

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_init_is_rejected(self, sdk):
        agent_server = FakeAgentServer(init_post_status=401)

        with pytest.raises(SandboxError):
            await _service(httpx_client=agent_server).start_sandbox()

        sdk.kill.assert_awaited_once()
        # Init is never retried: a replay cannot be told apart from a bad key.
        assert len(agent_server.init_post_bodies) == 1

    @pytest.mark.asyncio
    async def test_kills_the_sandbox_when_already_initialized(self, sdk):
        agent_server = FakeAgentServer(
            init_get_responses=[_response(200, {'state': 'ready'})]
        )

        with pytest.raises(SandboxError):
            await _service(httpx_client=agent_server).start_sandbox()

        sdk.kill.assert_awaited_once()


# ---------------------------------------------------------------------------
# Session API keys
# ---------------------------------------------------------------------------


class TestSessionApiKeys:
    @pytest.mark.asyncio
    async def test_round_trip(self, sdk):
        service = _service()
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key

        found = await service.get_sandbox_by_session_api_key(sandbox.session_api_key)

        assert found is not None
        assert found.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_key_carries_the_sandbox_id(self, sdk):
        sandbox = await _service().start_sandbox()

        assert sandbox.session_api_key
        assert sandbox.session_api_key.split('.')[0] == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_tampered_key_is_rejected_without_a_lookup(self, sdk):
        service = _service()
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
    async def test_key_for_another_sandbox_id_is_rejected(self, sdk):
        service = _service()
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key
        _, _, digest = sandbox.session_api_key.partition('.')

        assert await service.get_sandbox_by_session_api_key(f'iother.{digest}') is None

    @pytest.mark.asyncio
    async def test_malformed_key_is_rejected(self, sdk):
        service = _service()

        assert await service.get_sandbox_by_session_api_key('no-separator') is None
        assert await service.get_sandbox_by_session_api_key('') is None
        assert await service.get_sandbox_by_session_api_key('.digest') is None

    @pytest.mark.asyncio
    async def test_derived_with_the_default_key_and_verified_against_all(self, sdk):
        """A key issued before a rotation still resolves afterwards."""
        before = _service(jwt_service=_jwt_service('old-secret'))
        sandbox = await before.start_sandbox()
        assert sandbox.session_api_key

        after = _service(jwt_service=_jwt_service('old-secret', 'new-secret'))
        assert (
            await after.get_sandbox_by_session_api_key(sandbox.session_api_key)
        ) is not None
        # New sandboxes are issued keys from the new default.
        assert (await after.start_sandbox()).session_api_key != sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_unrelated_key_is_rejected(self, sdk):
        stranger = _service(jwt_service=_jwt_service('some-other-secret'))
        sandbox = await stranger.start_sandbox()
        assert sandbox.session_api_key

        service = _service(jwt_service=_jwt_service('the-secret'))
        assert (
            await service.get_sandbox_by_session_api_key(sandbox.session_api_key)
        ) is None

    @pytest.mark.asyncio
    async def test_record_lookup_returns_owner(self, sdk):
        service = _service()
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key

        record = await service.get_sandbox_record_by_session_api_key(
            sandbox.session_api_key
        )

        assert record is not None
        assert record.id == SANDBOX_ID
        assert record.created_by_user_id == OWNER_ID

    @pytest.mark.asyncio
    async def test_key_survives_pause_and_resume(self, sdk):
        """Same id, same host, process restored from the snapshot."""
        service = _service()
        sandbox = await service.start_sandbox()
        assert sandbox.session_api_key

        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        assert await service.pause_sandbox(SANDBOX_ID) is True
        assert await service.resume_sandbox(SANDBOX_ID) is True
        sdk.get_info.return_value = _e2b_info(state=SandboxState.RUNNING)

        resumed = await service.get_sandbox(SANDBOX_ID)
        assert resumed is not None
        assert resumed.session_api_key == sandbox.session_api_key


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class TestGetSandbox:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'state,expected',
        [
            (SandboxState.RUNNING, SandboxStatus.RUNNING),
            (SandboxState.PAUSED, SandboxStatus.PAUSED),
        ],
    )
    async def test_status_mapping(self, sdk, state, expected):
        sdk.get_info.return_value = _e2b_info(state=state)

        sandbox = await _service().get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.status == expected
        assert sandbox.created_at == CREATED_AT

    @pytest.mark.asyncio
    async def test_paused_sandbox_exposes_no_urls_or_key(self, sdk):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)

        sandbox = await _service().get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.session_api_key is None
        assert sandbox.exposed_urls is None

    @pytest.mark.asyncio
    async def test_missing_sandbox_returns_none(self, sdk):
        sdk.get_info.side_effect = SandboxNotFoundException('Sandbox not found')

        assert await _service().get_sandbox(SANDBOX_ID) is None

    @pytest.mark.asyncio
    async def test_malformed_id_returns_none(self, sdk):
        sdk.get_info.side_effect = SandboxException('400: Invalid sandbox ID')

        assert await _service().get_sandbox('not-an-id') is None

    @pytest.mark.asyncio
    async def test_unmanaged_sandbox_is_invisible(self, sdk):
        sdk.get_info.return_value = _e2b_info(managed=False)

        assert await _service(user_id=None).get_sandbox(SANDBOX_ID) is None


class TestExposedUrls:
    @pytest.mark.asyncio
    async def test_urls_are_built_from_id_and_domain(self, sdk):
        sandbox = await _service().get_sandbox(SANDBOX_ID)

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
    async def test_vscode_url_comes_from_the_agent_server(self, sdk):
        agent_server = FakeAgentServer()

        sandbox = await _service(httpx_client=agent_server).get_sandbox(SANDBOX_ID)

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
    async def test_vscode_url_is_cached(self, sdk):
        agent_server = FakeAgentServer()
        service = _service(httpx_client=agent_server)

        await service.get_sandbox(SANDBOX_ID)
        await service.get_sandbox(SANDBOX_ID)
        await service.get_sandbox(SANDBOX_ID)

        assert len(agent_server.vscode_requests) == 1

    @pytest.mark.asyncio
    async def test_vscode_url_is_populated_by_start_and_reused(self, sdk):
        agent_server = FakeAgentServer()
        service = _service(httpx_client=agent_server)

        await service.start_sandbox()
        await service.get_sandbox(SANDBOX_ID)

        assert len(agent_server.vscode_requests) == 1

    @pytest.mark.asyncio
    async def test_vscode_failure_omits_the_url(self, sdk):
        agent_server = FakeAgentServer(vscode_url=None)

        sandbox = await _service(httpx_client=agent_server).get_sandbox(SANDBOX_ID)

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
    async def test_delete_evicts_the_cached_url(self, sdk):
        agent_server = FakeAgentServer()
        service = _service(httpx_client=agent_server)
        await service.get_sandbox(SANDBOX_ID)

        await service.delete_sandbox(SANDBOX_ID)

        assert SANDBOX_ID not in e2b_sandbox_service._vscode_urls


class TestSearchSandboxes:
    @pytest.mark.asyncio
    async def test_filters_on_owner_metadata(self, sdk):
        sdk.list.return_value = _paginator([_e2b_info()])

        page = await _service().search_sandboxes()

        query = sdk.list.call_args.kwargs['query']
        assert query.metadata == {
            MANAGED_METADATA_KEY: 'true',
            CREATED_BY_USER_ID_METADATA_KEY: OWNER_ID,
        }
        assert set(query.state) == {SandboxState.RUNNING, SandboxState.PAUSED}
        assert [item.id for item in page.items] == [SANDBOX_ID]
        assert page.items[0].created_by_user_id == OWNER_ID
        assert page.items[0].sandbox_spec_id == TEMPLATE

    @pytest.mark.asyncio
    async def test_admin_sees_every_managed_sandbox(self, sdk):
        sdk.list.return_value = _paginator([_e2b_info(user_id=OTHER_USER_ID)])

        page = await _service(user_id=None).search_sandboxes()

        assert sdk.list.call_args.kwargs['query'].metadata == {
            MANAGED_METADATA_KEY: 'true'
        }
        assert [item.id for item in page.items] == [SANDBOX_ID]

    @pytest.mark.asyncio
    async def test_pagination_maps_the_next_token(self, sdk):
        sdk.list.return_value = _paginator([_e2b_info()], next_token='cursor-2')

        page = await _service().search_sandboxes(page_id='cursor-1', limit=25)

        assert sdk.list.call_args.kwargs['next_token'] == 'cursor-1'
        assert sdk.list.call_args.kwargs['limit'] == 25
        assert page.next_page_id == 'cursor-2'

    @pytest.mark.asyncio
    async def test_list_failure_returns_an_empty_page(self, sdk):
        paginator = _paginator([])
        paginator.next_items.side_effect = SandboxException('boom')
        sdk.list.return_value = paginator

        page = await _service().search_sandboxes()

        assert page.items == []
        assert page.next_page_id is None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_pause_uses_the_sdk(self, sdk):
        assert await _service().pause_sandbox(SANDBOX_ID) is True

        sdk.pause.assert_awaited_once()
        assert sdk.pause.await_args.args[0] == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_pause_of_a_paused_sandbox_is_a_no_op(self, sdk):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)

        assert await _service().pause_sandbox(SANDBOX_ID) is True

        sdk.pause.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_of_a_missing_sandbox_returns_false(self, sdk):
        sdk.get_info.side_effect = SandboxNotFoundException('gone')

        assert await _service().pause_sandbox(SANDBOX_ID) is False

    @pytest.mark.asyncio
    async def test_resume_connects_with_a_fresh_timeout(self, sdk):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)

        assert await _service().resume_sandbox(SANDBOX_ID) is True

        sdk.connect.assert_awaited_once()
        assert sdk.connect.await_args.args[0] == SANDBOX_ID
        assert sdk.connect.await_args.kwargs['timeout'] == 3600

    @pytest.mark.asyncio
    async def test_resume_of_a_missing_sandbox_returns_false(self, sdk):
        sdk.get_info.side_effect = SandboxNotFoundException('gone')

        assert await _service().resume_sandbox(SANDBOX_ID) is False
        sdk.connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_retries_while_the_snapshot_settles(self, sdk):
        """A sandbox reports paused before its snapshot can be placed."""
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = [
            SandboxException('500: Failed to place sandbox'),
            None,
        ]

        assert await _service().resume_sandbox(SANDBOX_ID) is True

        assert sdk.connect.await_count == 2

    @pytest.mark.asyncio
    async def test_resume_gives_up_after_the_retry_budget(self, sdk):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = SandboxException('500: Failed to place sandbox')

        assert await _service(resume_retries=3).resume_sandbox(SANDBOX_ID) is False

        assert sdk.connect.await_count == 3

    @pytest.mark.asyncio
    async def test_resume_does_not_retry_a_vanished_sandbox(self, sdk):
        sdk.get_info.return_value = _e2b_info(state=SandboxState.PAUSED)
        sdk.connect.side_effect = SandboxNotFoundException('Paused sandbox not found')

        assert await _service().resume_sandbox(SANDBOX_ID) is False

        assert sdk.connect.await_count == 1

    @pytest.mark.asyncio
    async def test_delete_kills_the_sandbox(self, sdk):
        assert await _service().delete_sandbox(SANDBOX_ID) is True

        sdk.kill.assert_awaited_once()
        assert sdk.kill.await_args.args[0] == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_delete_of_a_missing_sandbox_returns_false(self, sdk):
        sdk.get_info.side_effect = SandboxNotFoundException('gone')

        assert await _service().delete_sandbox(SANDBOX_ID) is False
        sdk.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_of_an_already_dead_sandbox_succeeds(self, sdk):
        """kill() reports False when there was nothing to kill; not an error."""
        sdk.kill.return_value = False

        assert await _service().delete_sandbox(SANDBOX_ID) is True

    @pytest.mark.asyncio
    async def test_delete_failure_is_retryable(self, sdk):
        sdk.kill.side_effect = SandboxException('service unavailable')

        with pytest.raises(SandboxDeleteRetryError):
            await _service().delete_sandbox(SANDBOX_ID)


# ---------------------------------------------------------------------------
# User scoping
# ---------------------------------------------------------------------------


class TestUserScoping:
    @pytest.fixture
    def intruder(self, sdk) -> E2BSandboxService:
        sdk.get_info.return_value = _e2b_info(user_id=OWNER_ID)
        return _service(user_id=OTHER_USER_ID)

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
    async def test_cannot_resolve_a_leaked_session_key(self, intruder, sdk):
        owner = _service(user_id=OWNER_ID)
        sandbox = await owner.start_sandbox()
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
    async def test_admin_can_get(self, sdk):
        sdk.get_info.return_value = _e2b_info(user_id=OWNER_ID)

        sandbox = await _service(user_id=None).get_sandbox(SANDBOX_ID)

        assert sandbox is not None
        assert sandbox.created_by_user_id == OWNER_ID
