"""Tests for SubstrateSandboxService.

ate-api-server is replaced by an in-memory fake that plays Substrate's actor
lifecycle; the agent server by a fake that records the requests the service
makes to it through the router. Focus areas:
- creating an actor from the template, with its egress policy, before it runs
- the sandbox table as the store for ownership, with user scoping
- the /api/init handshake, on start and again on a cold resume
- status mapping from the actor's state, including MISSING
- URLs through the app's proxy
"""

import json
import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.protobuf import json_format
from pydantic import SecretStr
from sqlalchemy import select

from openhands.agent_server.init_router import InitRequest
from openhands.app_server.errors import (
    AuthError,
    SandboxDeleteRetryError,
    SandboxError,
)
from openhands.app_server.sandbox import substrate_sandbox_service
from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
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
    SUBSTRATE_BACKEND,
    LifecycleState,
    StoredSandbox,
    hash_session_api_key,
)
from openhands.app_server.sandbox.substrate import ateapi_pb2 as pb
from openhands.app_server.sandbox.substrate_sandbox_service import (
    DEFAULT_EGRESS_RULES,
    SubstrateSandboxService,
    SubstrateSandboxServiceInjector,
    actor_status,
    parse_egress_rules,
)
from openhands.app_server.sandbox.substrate_sandbox_spec_service import (
    SubstrateSandboxSpecInfo,
)
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext

TEMPLATE = 'openhands-agent-server'
INIT_API_KEY = 'template-init-key'
WEB_URL = 'https://app.example.com'
PROXY = f'{WEB_URL}/api/v1/sandbox-proxy'
OWNER_ID = 'user-1'
OTHER_USER_ID = 'user-2'
ACTOR = 'oh-0123456789abcdef01234567'
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
SESSION_API_KEY = 'the-session-key'
RESUME_TIMEOUT = 7


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeControl:
    """SubstrateControl stand in that plays ate-api-server."""

    atespace = 'openhands'

    def __init__(self):
        self.actors: dict[str, pb.Actor] = {}
        self.calls: list[tuple] = []
        self.policies: dict[str, list[pb.EgressRule]] = {}
        self.create_error: Exception | None = None
        self.resume_error: Exception | None = None
        self.delete_error: Exception | None = None

    def add_actor(
        self, name: str = ACTOR, state: int = pb.ACTOR_STATE_RUNNING
    ) -> pb.Actor:
        actor = pb.Actor(
            metadata=pb.ResourceMetadata(atespace=self.atespace, name=name),
            actor_template=pb.ObjectRef(atespace=self.atespace, name=TEMPLATE),
            status=pb.ActorStatus(state=state),
        )
        self.actors[name] = actor
        return actor

    async def get_actor(self, name: str) -> pb.Actor | None:
        self.calls.append(('get', name))
        actor = self.actors.get(name)
        return pb.Actor.FromString(actor.SerializeToString()) if actor else None

    async def create_actor(self, name: str, template: str) -> pb.Actor:
        self.calls.append(('create', name, template))
        if self.create_error:
            raise self.create_error
        return self.add_actor(name, pb.ACTOR_STATE_SUSPENDED)

    async def create_egress_policy(self, name: str, rules) -> None:
        self.calls.append(('egress', name))
        self.policies[name] = list(rules)

    async def resume_actor(self, name: str, timeout: float) -> pb.Actor | None:
        self.calls.append(('resume', name, timeout))
        if self.resume_error:
            raise self.resume_error
        actor = self.actors.get(name)
        if actor is None:
            return None
        actor.status.state = pb.ACTOR_STATE_RUNNING
        return actor

    async def suspend_actor(self, name: str) -> pb.Actor | None:
        self.calls.append(('suspend', name))
        actor = self.actors.get(name)
        if actor is None:
            return None
        actor.status.state = pb.ACTOR_STATE_SUSPENDED
        return actor

    async def delete_actor(self, name: str) -> None:
        self.calls.append(('delete', name))
        if self.delete_error:
            raise self.delete_error
        self.actors.pop(name, None)

    def names(self, kind: str) -> list[str]:
        return [call[1] for call in self.calls if call[0] == kind]


def _response(status_code: int = 200, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload if payload is not None else {},
        request=httpx.Request('GET', 'http://actor'),
    )


class FakeAgentServer:
    """The router client: answers the agent server's routes on any actor."""

    def __init__(
        self,
        init_state: str = 'dormant',
        init_post_status: int = 200,
        init_get_responses: list | None = None,
    ):
        self.init_state = init_state
        self.init_post_status = init_post_status
        self.init_get_responses = init_get_responses or []
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict, dict]] = []

    async def get(self, url: str, **kwargs):
        self.gets.append(url)
        if url.endswith('/api/init'):
            if self.init_get_responses:
                result = self.init_get_responses.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return _response(200, {'state': self.init_state, 'error': None})
        if url.endswith('/alive'):
            return _response(200, {'status': 'ok'})
        if url.endswith('/server_info'):
            return _response(200, {'idle_time': 42.5})
        raise AssertionError(f'unexpected GET {url}')

    async def post(self, url: str, **kwargs):
        assert url.endswith('/api/init'), f'unexpected POST {url}'
        self.posts.append((url, kwargs['json'], kwargs.get('headers') or {}))
        return _response(self.init_post_status, {'state': 'ready'})


def _stored(
    sandbox_id: str = ACTOR,
    created_by_user_id: str | None = OWNER_ID,
    session_api_key: str | None = SESSION_API_KEY,
    lifecycle_state: LifecycleState = LifecycleState.RUNNING,
) -> StoredSandbox:
    return StoredSandbox(
        id=sandbox_id,
        backend=SUBSTRATE_BACKEND,
        created_by_user_id=created_by_user_id,
        sandbox_spec_id=TEMPLATE,
        session_api_key_hash=(
            hash_session_api_key(session_api_key) if session_api_key else None
        ),
        session_api_key=SecretStr(session_api_key) if session_api_key else None,
        created_at=CREATED_AT,
        lifecycle_state=lifecycle_state,
    )


def _user_context(user_id: str | None) -> AsyncMock:
    context = AsyncMock(spec=UserContext)
    context.get_user_id.return_value = user_id
    context.get_default_sandbox_spec_id.return_value = None
    return context


def _service(
    db_session,
    control: FakeControl,
    agent_server: FakeAgentServer | None = None,
    user_id: str | None = OWNER_ID,
    user_context: UserContext | None = None,
    init_api_key: str | None = INIT_API_KEY,
    proxy_url: str = WEB_URL,
    web_url: str | None = WEB_URL,
    webhook_base_url: str | None = None,
    lifecycle: SandboxLifecycleSettings | None = None,
) -> SubstrateSandboxService:
    spec = SubstrateSandboxSpecInfo(
        id=TEMPLATE,
        command=None,
        working_dir='/workspace/project',
        init_api_key=SecretStr(init_api_key) if init_api_key else None,
    )
    return SubstrateSandboxService(
        sandbox_spec_service=PresetSandboxSpecService(specs=[spec]),
        user_context=user_context or _user_context(user_id),
        httpx_client=MagicMock(),
        db_session=db_session,
        control=control,  # type: ignore[arg-type]
        router_client=agent_server or FakeAgentServer(),  # type: ignore[arg-type]
        proxy_url=proxy_url,
        egress_rules=parse_egress_rules(json.dumps(DEFAULT_EGRESS_RULES)),
        max_num_sandboxes=10,
        resume_timeout_seconds=RESUME_TIMEOUT,
        init_timeout_seconds=5,
        poll_interval=0,
        web_url=web_url,
        webhook_base_url=webhook_base_url,
        lifecycle=lifecycle or SandboxLifecycleSettings(),
    )


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def control():
    return FakeControl()


@pytest.fixture
def store(db_session):
    async def _store(*sandboxes: StoredSandbox) -> None:
        for sandbox in sandboxes:
            db_session.add(sandbox)
        await db_session.flush()

    return _store


async def _rows(db_session) -> list[str]:
    await db_session.flush()
    return list((await db_session.execute(select(StoredSandbox.id))).scalars())


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


class TestStartSandbox:
    @pytest.mark.asyncio
    async def test_creates_the_actor_with_its_policy_before_it_runs(
        self, control, db_session
    ):
        sandbox = await _service(db_session, control).start_sandbox()

        assert control.calls == [
            ('create', sandbox.id, TEMPLATE),
            ('egress', sandbox.id),
            ('resume', sandbox.id, RESUME_TIMEOUT),
        ]
        assert sandbox.status == SandboxStatus.RUNNING
        assert sandbox.created_by_user_id == OWNER_ID
        assert sandbox.sandbox_spec_id == TEMPLATE
        assert sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_actor_name_is_a_dns_label_the_app_picks(self, control, db_session):
        sandbox = await _service(db_session, control).start_sandbox(
            sandbox_id='Requested_ID'
        )

        assert re.fullmatch(r'oh-[0-9a-f]{24}', sandbox.id)

    @pytest.mark.asyncio
    async def test_default_policy_allows_every_host(self, control, db_session):
        sandbox = await _service(db_session, control).start_sandbox()

        tls, http = control.policies[sandbox.id]
        assert list(tls.tls_passthrough.hostnames) == ['*']
        assert list(tls.tls_passthrough.ports.numbers) == [443]
        assert list(http.http.hostnames) == ['*']
        assert http.http.ports.HasField('all')

    @pytest.mark.asyncio
    async def test_writes_the_row(self, control, db_session):
        sandbox = await _service(db_session, control).start_sandbox()

        row = await db_session.get(StoredSandbox, sandbox.id)
        assert row.backend == SUBSTRATE_BACKEND
        assert row.created_by_user_id == OWNER_ID
        assert row.sandbox_spec_id == TEMPLATE
        assert row.session_api_key_hash == hash_session_api_key(sandbox.session_api_key)
        assert row.session_api_key.get_secret_value() == sandbox.session_api_key
        assert row.lifecycle_state == LifecycleState.RUNNING

    @pytest.mark.asyncio
    async def test_urls_go_through_the_proxy(self, control, db_session):
        sandbox = await _service(db_session, control).start_sandbox()

        urls = {url.name: url.url for url in sandbox.exposed_urls}
        assert urls == {
            AGENT_SERVER: f'{PROXY}/{sandbox.id}/8000',
            WORKER_1: f'{PROXY}/{sandbox.id}/8011',
            WORKER_2: f'{PROXY}/{sandbox.id}/8012',
            VSCODE: (
                f'{PROXY}/{sandbox.id}/8001/?tkn={sandbox.session_api_key}'
                '&folder=/workspace/project'
            ),
        }

    @pytest.mark.asyncio
    async def test_needs_an_owner(self, control, db_session):
        service = _service(db_session, control, user_context=ADMIN)

        with (
            patch.object(service, 'pause_old_sandboxes') as pause,
            pytest.raises(AuthError),
        ):
            await service.start_sandbox()

        pause.assert_not_called()
        assert control.calls == []

    @pytest.mark.asyncio
    async def test_needs_an_init_key_before_creating(self, control, db_session):
        with pytest.raises(SandboxError, match='SUBSTRATE_INIT_API_KEY'):
            await _service(db_session, control, init_api_key=None).start_sandbox()

        assert control.calls == []

    @pytest.mark.asyncio
    async def test_needs_a_proxy_url(self, control, db_session):
        with pytest.raises(SandboxError, match='SUBSTRATE_PROXY_URL'):
            await _service(db_session, control, proxy_url='').start_sandbox()

        assert control.calls == []

    @pytest.mark.asyncio
    async def test_failed_create_writes_no_row(self, control, db_session):
        control.create_error = SandboxError('ActorTemplate cannot be used')

        with pytest.raises(SandboxError, match='ActorTemplate'):
            await _service(db_session, control).start_sandbox()

        assert await _rows(db_session) == []

    @pytest.mark.asyncio
    async def test_failed_resume_deletes_the_actor(self, control, db_session):
        control.resume_error = SandboxError('Could not resume actor')

        with pytest.raises(SandboxError, match='Could not resume'):
            await _service(db_session, control).start_sandbox()

        assert control.names('delete') == control.names('create')
        assert control.actors == {}

    @pytest.mark.asyncio
    async def test_rejected_init_key_deletes_the_actor(self, control, db_session):
        agent_server = FakeAgentServer(init_post_status=401)

        with pytest.raises(SandboxError, match='rejected the init API key'):
            await _service(db_session, control, agent_server).start_sandbox()

        assert control.actors == {}

    @pytest.mark.asyncio
    async def test_already_initialized_server_is_refused(self, control, db_session):
        agent_server = FakeAgentServer(init_state='ready')

        with pytest.raises(SandboxError, match='already initialized'):
            await _service(db_session, control, agent_server).start_sandbox()

        assert agent_server.posts == []
        assert control.actors == {}

    @pytest.mark.asyncio
    async def test_waits_until_the_agent_server_answers(self, control, db_session):
        agent_server = FakeAgentServer(
            init_get_responses=[
                _response(503, {'detail': 'starting'}),
                httpx.ConnectError('tunnel refused'),
            ]
        )

        sandbox = await _service(db_session, control, agent_server).start_sandbox()

        assert sandbox.status == SandboxStatus.RUNNING
        assert agent_server.gets == [f'http://{sandbox.id}:8000/api/init'] * 3


class TestInitHandshake:
    @pytest.mark.asyncio
    async def test_init_body_is_accepted_by_the_agent_server(self, control, db_session):
        agent_server = FakeAgentServer()

        sandbox = await _service(db_session, control, agent_server).start_sandbox()

        url, body, headers = agent_server.posts[0]
        InitRequest.model_validate(body)
        assert url == f'http://{sandbox.id}:8000/api/init'
        assert headers == {'X-Init-API-Key': INIT_API_KEY}
        assert body['session_api_keys'] == [sandbox.session_api_key]
        assert body['secret_key'] == sandbox.session_api_key
        assert body['conversations_path'] == '/var/openhands/conversations'
        assert body['bash_events_dir'] == '/var/openhands/bash_events'
        assert body['conversation_worktree_root'] == '/workspace/worktrees'
        assert body['webhooks'] == [{'base_url': f'{WEB_URL}/api/v1/webhooks'}]
        assert body['allow_cors_origins'] == [WEB_URL]
        assert body['env'][WORKER_1] == '8011'
        assert body['env'][WORKER_2] == '8012'

    @pytest.mark.asyncio
    async def test_webhook_base_url_overrides_the_web_url(self, control, db_session):
        agent_server = FakeAgentServer()

        await _service(
            db_session,
            control,
            agent_server,
            webhook_base_url='http://openhands.openhands.svc:3000/',
        ).start_sandbox()

        assert agent_server.posts[0][1]['webhooks'] == [
            {'base_url': 'http://openhands.openhands.svc:3000/api/v1/webhooks'}
        ]

    @pytest.mark.asyncio
    async def test_no_webhook_without_a_reachable_url(self, control, db_session):
        agent_server = FakeAgentServer()

        with patch.object(substrate_sandbox_service._logger, 'warning') as warning:
            await _service(
                db_session, control, agent_server, web_url='http://localhost:3000'
            ).start_sandbox()

        assert 'webhooks' not in agent_server.posts[0][1]
        warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_extra_env_wins(self, control, db_session):
        agent_server = FakeAgentServer()

        await _service(db_session, control, agent_server).start_sandbox(
            extra_env={WORKER_1: '9000', 'FOO': 'bar'}
        )

        env = agent_server.posts[0][1]['env']
        assert env[WORKER_1] == '9000'
        assert env['FOO'] == 'bar'


# ---------------------------------------------------------------------------
# Status and reads
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.parametrize(
        ('state', 'expected'),
        [
            (pb.ACTOR_STATE_RESUMING, SandboxStatus.STARTING),
            (pb.ACTOR_STATE_RUNNING, SandboxStatus.RUNNING),
            (pb.ACTOR_STATE_SUSPENDING, SandboxStatus.PAUSED),
            (pb.ACTOR_STATE_SUSPENDED, SandboxStatus.PAUSED),
            (pb.ACTOR_STATE_REVERTING, SandboxStatus.PAUSED),
            (pb.ACTOR_STATE_CRASHED, SandboxStatus.ERROR),
            (pb.ACTOR_STATE_DELETING, SandboxStatus.MISSING),
        ],
    )
    def test_maps_the_actor_state(self, state, expected):
        actor = pb.Actor(status=pb.ActorStatus(state=state))

        assert actor_status(actor) == expected

    def test_no_actor_is_missing(self):
        assert actor_status(None) == SandboxStatus.MISSING

    @pytest.mark.asyncio
    async def test_running_carries_the_key_and_urls(self, control, db_session, store):
        await store(_stored())
        control.add_actor()

        sandbox = await _service(db_session, control).get_sandbox(ACTOR)

        assert sandbox.status == SandboxStatus.RUNNING
        assert sandbox.session_api_key == SESSION_API_KEY
        assert sandbox.exposed_urls

    @pytest.mark.asyncio
    async def test_paused_hides_the_key_and_urls(self, control, db_session, store):
        await store(_stored())
        control.add_actor(state=pb.ACTOR_STATE_SUSPENDED)

        sandbox = await _service(db_session, control).get_sandbox(ACTOR)

        assert sandbox.status == SandboxStatus.PAUSED
        assert sandbox.session_api_key is None
        assert sandbox.exposed_urls is None

    @pytest.mark.asyncio
    async def test_crash_carries_its_message(self, control, db_session, store):
        await store(_stored())
        actor = control.add_actor(state=pb.ACTOR_STATE_CRASHED)
        actor.status.crash.message = 'wakeup probe timed out'

        sandbox = await _service(db_session, control).get_sandbox(ACTOR)

        assert sandbox.status == SandboxStatus.ERROR
        assert sandbox.status_detail == 'wakeup probe timed out'

    @pytest.mark.asyncio
    async def test_row_without_an_actor_is_missing(self, control, db_session, store):
        await store(_stored())

        sandbox = await _service(db_session, control).get_sandbox(ACTOR)

        assert sandbox.status == SandboxStatus.MISSING

    @pytest.mark.asyncio
    async def test_session_key_lookup(self, control, db_session, store):
        await store(_stored())
        control.add_actor()
        service = _service(db_session, control, user_context=ADMIN)

        sandbox = await service.get_sandbox_by_session_api_key(SESSION_API_KEY)
        record = await service.get_sandbox_record_by_session_api_key(SESSION_API_KEY)

        assert sandbox.id == ACTOR
        assert record.id == ACTOR
        assert record.created_by_user_id == OWNER_ID
        assert await service.get_sandbox_by_session_api_key('wrong') is None


class TestUserScoping:
    @pytest.mark.asyncio
    async def test_other_user_cannot_see_the_sandbox(self, control, db_session, store):
        await store(_stored())
        control.add_actor()

        service = _service(db_session, control, user_id=OTHER_USER_ID)

        assert await service.get_sandbox(ACTOR) is None
        assert (await service.search_sandboxes()).items == []
        assert await service.pause_sandbox(ACTOR) is False
        assert await service.resume_sandbox(ACTOR) is False
        assert await service.delete_sandbox(ACTOR) is False
        assert control.calls == []

    @pytest.mark.asyncio
    async def test_admin_sees_every_owner(self, control, db_session, store):
        await store(_stored(), _stored('oh-other', created_by_user_id=OTHER_USER_ID))
        control.add_actor()
        control.add_actor('oh-other')

        page = await _service(
            db_session, control, user_context=ADMIN
        ).search_sandboxes()

        assert {sandbox.id for sandbox in page.items} == {ACTOR, 'oh-other'}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_suspends_the_actor(self, control, db_session, store):
        await store(_stored())
        control.add_actor()

        assert await _service(db_session, control).pause_sandbox(ACTOR) is True

        assert control.actors[ACTOR].status.state == pb.ACTOR_STATE_SUSPENDED
        row = await db_session.get(StoredSandbox, ACTOR)
        assert row.lifecycle_state == LifecycleState.PAUSED

    @pytest.mark.asyncio
    async def test_pause_of_a_suspended_actor_marks_the_row(
        self, control, db_session, store
    ):
        await store(_stored())
        control.add_actor(state=pb.ACTOR_STATE_SUSPENDED)

        assert await _service(db_session, control).pause_sandbox(ACTOR) is True

        assert control.names('suspend') == []
        row = await db_session.get(StoredSandbox, ACTOR)
        assert row.lifecycle_state == LifecycleState.PAUSED

    @pytest.mark.asyncio
    async def test_pause_of_a_missing_actor(self, control, db_session, store):
        await store(_stored())

        assert await _service(db_session, control).pause_sandbox(ACTOR) is False

    @pytest.mark.asyncio
    async def test_resume_restores_without_a_new_init(self, control, db_session, store):
        """A resume restores memory, so the agent server is already ready."""
        await store(_stored(lifecycle_state=LifecycleState.PAUSED))
        control.add_actor(state=pb.ACTOR_STATE_SUSPENDED)
        agent_server = FakeAgentServer(init_state='ready')

        assert (
            await _service(db_session, control, agent_server).resume_sandbox(ACTOR)
            is True
        )

        assert control.names('resume') == [ACTOR]
        assert agent_server.posts == []
        row = await db_session.get(StoredSandbox, ACTOR)
        assert row.lifecycle_state == LifecycleState.RUNNING

    @pytest.mark.asyncio
    async def test_cold_resume_initializes_with_the_same_key(
        self, control, db_session, store
    ):
        await store(_stored(lifecycle_state=LifecycleState.PAUSED))
        control.add_actor(state=pb.ACTOR_STATE_SUSPENDED)
        agent_server = FakeAgentServer(init_state='dormant')

        await _service(db_session, control, agent_server).resume_sandbox(ACTOR)

        body = agent_server.posts[0][1]
        assert body['session_api_keys'] == [SESSION_API_KEY]
        assert body['secret_key'] == SESSION_API_KEY

    @pytest.mark.asyncio
    async def test_resume_of_a_running_actor_does_nothing(
        self, control, db_session, store
    ):
        await store(_stored())
        control.add_actor()

        assert await _service(db_session, control).resume_sandbox(ACTOR) is True

        assert control.names('resume') == []

    @pytest.mark.asyncio
    async def test_resume_of_a_missing_actor(self, control, db_session, store):
        await store(_stored(lifecycle_state=LifecycleState.PAUSED))

        assert await _service(db_session, control).resume_sandbox(ACTOR) is False


class TestDelete:
    @pytest.mark.asyncio
    async def test_deletes_the_actor_and_the_row(self, control, db_session, store):
        await store(_stored())
        control.add_actor()

        assert await _service(db_session, control).delete_sandbox(ACTOR) is True

        assert control.actors == {}
        assert await _rows(db_session) == []

    @pytest.mark.asyncio
    async def test_failed_delete_keeps_the_row(self, control, db_session, store):
        await store(_stored())
        control.add_actor()
        control.delete_error = SandboxError('Could not delete actor')

        with pytest.raises(SandboxDeleteRetryError):
            await _service(db_session, control).delete_sandbox(ACTOR)

        assert await _rows(db_session) == [ACTOR]


class TestProbes:
    @pytest.mark.asyncio
    async def test_idle_time_goes_through_the_router(self, control, db_session, store):
        await store(_stored())
        control.add_actor()
        agent_server = FakeAgentServer()
        service = _service(db_session, control, agent_server)

        sandbox = await service.get_sandbox(ACTOR)

        assert await service.get_idle_time(sandbox) == 42.5
        assert agent_server.gets == [f'http://{ACTOR}:8000/server_info']

    @pytest.mark.asyncio
    async def test_alive_goes_through_the_router(self, control, db_session, store):
        await store(_stored())
        control.add_actor()
        agent_server = FakeAgentServer()
        service = _service(db_session, control, agent_server)

        sandbox = await service.get_sandbox(ACTOR)

        assert await service._check_agent_server_alive(sandbox, MagicMock()) is True
        assert agent_server.gets == [f'http://{ACTOR}:8000/alive']


class TestInjector:
    def test_reads_its_settings_from_env(self, monkeypatch):
        rules = [{'tlsPassthrough': {'hostnames': ['api.openai.com']}}]
        monkeypatch.setenv('SUBSTRATE_ATESPACE', 'team-a')
        monkeypatch.setenv('SUBSTRATE_ROUTER_ADDRESS', 'localhost:18444')
        monkeypatch.setenv(
            'SUBSTRATE_ROUTER_SERVER_NAME', 'atenet-router.ate-system.svc'
        )
        monkeypatch.setenv('SUBSTRATE_EGRESS_RULES', json.dumps(rules))

        injector = SubstrateSandboxServiceInjector()
        router = injector.router_settings()

        assert injector.atespace == 'team-a'
        assert router.address == 'localhost:18444'
        assert router.server_name == 'atenet-router.ate-system.svc'
        assert router.atespace == 'team-a'
        parsed = parse_egress_rules(injector.egress_rules)
        assert list(parsed[0].tls_passthrough.hostnames) == ['api.openai.com']

    def test_rejects_a_malformed_rule(self):
        with pytest.raises(json_format.ParseError):
            parse_egress_rules(json.dumps([{'smtp': {}}]))
