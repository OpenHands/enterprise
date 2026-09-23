"""Tests for K8sAgentSandboxService.

The agent-sandbox API is replaced by an in-memory fake that plays the
controller; the agent server by a fake that records the requests the service
makes to it. Focus areas:
- claims on the warm pool, and the sandbox table as the store for ownership
- user scoping, including cross user isolation and the admin (no user id) case
- the /api/init handshake, on start and again on resume
- status mapping from the claim's Ready condition, including MISSING
- VSCode URLs under the router path
- the Kubernetes API error mapping
"""

import copy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import urllib3
from kubernetes import client as k8s_client
from kubernetes.client.exceptions import ApiException
from pydantic import SecretStr
from sqlalchemy import select

from openhands.agent_server.init_router import InitRequest
from openhands.app_server.errors import (
    AuthError,
    SandboxDeleteRetryError,
    SandboxError,
)
from openhands.app_server.sandbox import k8s_agent_sandbox_service
from openhands.app_server.sandbox.k8s_agent_sandbox_service import (
    MANAGED_LABEL,
    OWNER_LABEL,
    SANDBOX_SPEC_ID_ANNOTATION,
    USER_ID_ANNOTATION,
    WORKER_1_PORT,
    WORKER_2_PORT,
    AgentSandboxClient,
    K8sAgentSandboxService,
    owner_label_value,
)
from openhands.app_server.sandbox.k8s_agent_sandbox_spec_service import (
    K8sAgentSandboxSpecInfo,
)
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
    K8S_AGENT_SANDBOX_BACKEND,
    StoredSandbox,
    hash_session_api_key,
)
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext

NAMESPACE = 'openhands-sandboxes'
POOL = 'openhands-agent-server'
INIT_API_KEY = 'template-init-key'
ROUTER_URL = 'https://app.example.com/sandbox-router'
WEB_URL = 'https://app.example.com'
OWNER_ID = 'user-1'
OTHER_USER_ID = 'user-2'
CLAIM_NAME = 'oh-claimed'
SANDBOX_NAME = 'openhands-agent-server-abcde'
AGENT_SERVER_URL = f'{ROUTER_URL}/{NAMESPACE}/{SANDBOX_NAME}/8000'
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
SESSION_API_KEY = 'the-session-key'


def _vscode_url(session_api_key: str) -> str:
    return (
        f'{ROUTER_URL}/{NAMESPACE}/{SANDBOX_NAME}/8001/?tkn={session_api_key}'
        '&folder=/workspace/project'
    )


VSCODE_URL = _vscode_url(SESSION_API_KEY)

READY = {'type': 'Ready', 'status': 'True', 'reason': 'DependenciesReady'}
SUSPENDED = {'type': 'Ready', 'status': 'False', 'reason': 'SandboxSuspended'}
BOOTING = {'type': 'Ready', 'status': 'False', 'reason': 'DependenciesNotReady'}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _matches(labels: dict, selector: str) -> bool:
    wanted = dict(term.split('=', 1) for term in selector.split(','))
    return all(labels.get(key) == value for key, value in wanted.items())


class FakeAgentSandbox:
    """AgentSandboxClient stand in that plays the agent-sandbox controller.

    A new claim adopts a ready warm sandbox at once, unless ``new_claim_status``
    says what the controller reports instead.
    """

    namespace = NAMESPACE

    def __init__(self, new_claim_status: dict | None = None):
        self.claims: dict[str, dict] = {}
        self.sandboxes: dict[str, dict] = {}
        self.new_claim_status = new_claim_status
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.modes: list[tuple[str, str]] = []
        self.selectors: list[str] = []
        self.delete_error: Exception | None = None

    def add_claim(
        self,
        name: str = CLAIM_NAME,
        owner: str = OWNER_ID,
        ready: dict | None = READY,
        sandbox_name: str | None = SANDBOX_NAME,
    ) -> dict:
        status: dict = {'conditions': [ready] if ready else []}
        if sandbox_name:
            status['sandbox'] = {'name': sandbox_name}
            self.sandboxes[sandbox_name] = {
                'metadata': {'name': sandbox_name, 'generation': 1},
                'spec': {'operatingMode': 'Running'},
                'status': {
                    'conditions': [{**(ready or BOOTING), 'observedGeneration': 1}]
                },
            }
        claim = {
            'metadata': {
                'name': name,
                'labels': {
                    MANAGED_LABEL: 'true',
                    OWNER_LABEL: owner_label_value(owner),
                },
            },
            'spec': {'warmPoolRef': {'name': POOL}},
            'status': status,
        }
        self.claims[name] = claim
        return claim

    async def create_claim(self, body: dict) -> dict:
        self.created.append(copy.deepcopy(body))
        name = body['metadata']['name']
        claim = copy.deepcopy(body)
        if self.new_claim_status is None:
            self.add_claim(name, body['metadata']['annotations'][USER_ID_ANNOTATION])
            claim['status'] = self.claims[name]['status']
        else:
            claim['status'] = copy.deepcopy(self.new_claim_status)
        self.claims[name] = claim
        return copy.deepcopy(claim)

    async def get_claim(self, name: str) -> dict | None:
        return copy.deepcopy(self.claims.get(name))

    async def list_claims(self, label_selector: str) -> list[dict]:
        self.selectors.append(label_selector)
        return [
            copy.deepcopy(claim)
            for claim in self.claims.values()
            if _matches(claim['metadata'].get('labels', {}), label_selector)
        ]

    async def delete_claim(self, name: str) -> bool:
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(name)
        return self.claims.pop(name, None) is not None

    async def get_sandbox(self, name: str) -> dict | None:
        return copy.deepcopy(self.sandboxes.get(name))

    async def set_operating_mode(self, name: str, mode: str) -> dict | None:
        """Apply the mode, then do what the controller does once the pod settles."""
        sandbox = self.sandboxes.get(name)
        if sandbox is None:
            return None
        self.modes.append((name, mode))
        sandbox['spec']['operatingMode'] = mode
        sandbox['metadata']['generation'] += 1
        ready = SUSPENDED if mode == 'Suspended' else READY
        generation = sandbox['metadata']['generation']
        sandbox['status']['conditions'] = [{**ready, 'observedGeneration': generation}]
        for claim in self.claims.values():
            if claim['status'].get('sandbox', {}).get('name') == name:
                claim['status']['conditions'] = [ready]
        return copy.deepcopy(sandbox)


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
    """httpx.AsyncClient stand in that answers the agent server's routes."""

    def __init__(
        self,
        init_get_responses: list | None = None,
        init_state: str = 'dormant',
        init_post_status: int = 200,
    ):
        self.init_get_responses = init_get_responses or []
        self.init_state = init_state
        self.init_post_status = init_post_status
        self.init_get_urls: list[str] = []
        self.init_post_urls: list[str] = []
        self.init_post_bodies: list[dict] = []
        self.init_post_headers: list[dict] = []

    async def get(self, url: str, **kwargs):
        assert url.endswith('/api/init'), f'unexpected GET {url}'
        self.init_get_urls.append(url)
        if self.init_get_responses:
            result = self.init_get_responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return _response(200, {'state': self.init_state, 'error': None})

    async def post(self, url: str, **kwargs):
        assert url.endswith('/api/init'), f'unexpected POST {url}'
        self.init_post_urls.append(url)
        self.init_post_bodies.append(kwargs['json'])
        self.init_post_headers.append(kwargs.get('headers') or {})
        return _response(self.init_post_status, {'state': 'ready'})


def _stored(
    sandbox_id: str = CLAIM_NAME,
    created_by_user_id: str | None = OWNER_ID,
    session_api_key: str | None = SESSION_API_KEY,
) -> StoredSandbox:
    """The sandbox table row start_sandbox writes for a claim."""
    return StoredSandbox(
        id=sandbox_id,
        backend=K8S_AGENT_SANDBOX_BACKEND,
        created_by_user_id=created_by_user_id,
        sandbox_spec_id=POOL,
        session_api_key_hash=(
            hash_session_api_key(session_api_key) if session_api_key else None
        ),
        session_api_key=SecretStr(session_api_key) if session_api_key else None,
        created_at=CREATED_AT,
    )


async def _row_exists(db_session, sandbox_id: str) -> bool:
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
    k8s: FakeAgentSandbox,
    user_id: str | None = OWNER_ID,
    user_context: UserContext | None = None,
    httpx_client=None,
    init_api_key: str | None = INIT_API_KEY,
    router_url: str = ROUTER_URL,
    web_url: str | None = WEB_URL,
    webhook_base_url: str | None = None,
    claim_timeout_seconds: int = 5,
) -> K8sAgentSandboxService:
    spec = K8sAgentSandboxSpecInfo(
        id=POOL,
        command=None,
        working_dir='/workspace/project',
        init_api_key=SecretStr(init_api_key) if init_api_key else None,
    )
    return K8sAgentSandboxService(
        sandbox_spec_service=PresetSandboxSpecService(specs=[spec]),
        user_context=user_context or _user_context(user_id),
        httpx_client=httpx_client or FakeAgentServer(),
        db_session=db_session,
        k8s=k8s,  # type: ignore[arg-type]
        router_url=router_url,
        max_num_sandboxes=10,
        claim_timeout_seconds=claim_timeout_seconds,
        init_timeout_seconds=5,
        poll_interval=0,
        web_url=web_url,
        webhook_base_url=webhook_base_url,
    )


@pytest.fixture
async def db_session(async_session_maker):
    """A session on this test's own postgres database."""
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def k8s():
    return FakeAgentSandbox()


@pytest.fixture
def store(db_session):
    async def _store(*sandboxes: StoredSandbox) -> None:
        for sandbox in sandboxes:
            db_session.add(sandbox)
        await db_session.flush()

    return _store


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


class TestStartSandbox:
    @pytest.mark.asyncio
    async def test_claims_from_the_pool_and_tags_the_claim(self, k8s, db_session):
        sandbox = await _service(db_session, k8s).start_sandbox()

        body = k8s.created[0]
        assert body['spec'] == {'warmPoolRef': {'name': POOL}}
        assert body['metadata']['labels'] == {
            MANAGED_LABEL: 'true',
            OWNER_LABEL: owner_label_value(OWNER_ID),
        }
        assert body['metadata']['annotations'] == {
            USER_ID_ANNOTATION: OWNER_ID,
            SANDBOX_SPEC_ID_ANNOTATION: POOL,
        }
        assert sandbox.id == body['metadata']['name']
        assert sandbox.status == SandboxStatus.RUNNING
        assert sandbox.created_by_user_id == OWNER_ID
        assert sandbox.sandbox_spec_id == POOL
        assert sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_claim_name_is_a_dns_label(self, k8s, db_session):
        sandbox = await _service(db_session, k8s).start_sandbox(
            sandbox_id='Not_A-Label'
        )

        assert sandbox.id.startswith('oh-')
        assert len(sandbox.id) <= 63
        assert sandbox.id == sandbox.id.lower()

    @pytest.mark.asyncio
    async def test_writes_the_row(self, k8s, db_session):
        sandbox = await _service(db_session, k8s).start_sandbox()

        row = await db_session.get(StoredSandbox, sandbox.id)
        assert row.backend == K8S_AGENT_SANDBOX_BACKEND
        assert row.created_by_user_id == OWNER_ID
        assert row.sandbox_spec_id == POOL
        assert row.session_api_key_hash == hash_session_api_key(sandbox.session_api_key)
        assert row.session_api_key.get_secret_value() == sandbox.session_api_key

    @pytest.mark.asyncio
    async def test_urls_go_through_the_router(self, k8s, db_session):
        sandbox = await _service(db_session, k8s).start_sandbox()

        urls = {url.name: url.url for url in sandbox.exposed_urls}
        assert urls == {
            AGENT_SERVER: AGENT_SERVER_URL,
            WORKER_1: f'{ROUTER_URL}/{NAMESPACE}/{SANDBOX_NAME}/{WORKER_1_PORT}',
            WORKER_2: f'{ROUTER_URL}/{NAMESPACE}/{SANDBOX_NAME}/{WORKER_2_PORT}',
            VSCODE: _vscode_url(sandbox.session_api_key),
        }

    @pytest.mark.asyncio
    async def test_needs_an_owner(self, k8s, db_session):
        """ADMIN has no user id, so it must not start a sandbox or pause any."""
        service = _service(db_session, k8s, user_context=ADMIN)

        with (
            patch.object(service, 'pause_old_sandboxes') as pause,
            pytest.raises(AuthError),
        ):
            await service.start_sandbox()

        pause.assert_not_called()
        assert k8s.created == []

    @pytest.mark.asyncio
    async def test_needs_an_init_key_before_claiming(self, k8s, db_session):
        with pytest.raises(SandboxError, match='AGENT_SANDBOX_INIT_API_KEY'):
            await _service(db_session, k8s, init_api_key=None).start_sandbox()

        assert k8s.created == []

    @pytest.mark.asyncio
    async def test_needs_a_router_url(self, k8s, db_session):
        with pytest.raises(SandboxError, match='AGENT_SANDBOX_ROUTER_URL'):
            await _service(db_session, k8s, router_url='').start_sandbox()

        assert k8s.created == []

    @pytest.mark.asyncio
    async def test_missing_warm_pool_names_the_pool(self, db_session):
        k8s = FakeAgentSandbox(
            new_claim_status={
                'conditions': [
                    {'type': 'Ready', 'status': 'False', 'reason': 'WarmPoolNotFound'}
                ]
            }
        )

        with pytest.raises(SandboxError, match=f"SandboxWarmPool '{POOL}'"):
            await _service(db_session, k8s).start_sandbox()

        assert k8s.claims == {}

    @pytest.mark.asyncio
    async def test_failed_claim_is_deleted(self, db_session):
        failed = {
            'type': 'Ready',
            'status': 'False',
            'reason': 'EnvVarsInjectionRejected',
            'message': 'claims may not set env',
        }
        k8s = FakeAgentSandbox(new_claim_status={'conditions': [failed]})

        with pytest.raises(SandboxError, match='EnvVarsInjectionRejected'):
            await _service(db_session, k8s).start_sandbox()

        assert k8s.claims == {}

    @pytest.mark.asyncio
    async def test_claim_that_never_gets_ready_times_out(self, db_session):
        k8s = FakeAgentSandbox(new_claim_status={'conditions': [BOOTING]})

        with pytest.raises(SandboxError, match='DependenciesNotReady'):
            await _service(db_session, k8s, claim_timeout_seconds=0).start_sandbox()

        assert k8s.claims == {}

    @pytest.mark.asyncio
    async def test_rejected_init_key_deletes_the_claim(self, k8s, db_session):
        agent_server = FakeAgentServer(init_post_status=401)

        with pytest.raises(SandboxError, match='rejected the init API key'):
            await _service(db_session, k8s, httpx_client=agent_server).start_sandbox()

        assert k8s.claims == {}

    @pytest.mark.asyncio
    async def test_already_initialized_pod_is_refused(self, k8s, db_session):
        agent_server = FakeAgentServer(init_state='ready')

        with pytest.raises(SandboxError, match='already initialized'):
            await _service(db_session, k8s, httpx_client=agent_server).start_sandbox()

        assert agent_server.init_post_bodies == []
        assert k8s.claims == {}

    @pytest.mark.asyncio
    async def test_waits_out_the_router_until_the_pod_answers(self, k8s, db_session):
        agent_server = FakeAgentServer(
            init_get_responses=[
                _response(502, {'detail': 'Could not connect to the backend sandbox'}),
                httpx.ConnectError('connection refused'),
            ]
        )

        sandbox = await _service(
            db_session, k8s, httpx_client=agent_server
        ).start_sandbox()

        assert sandbox.status == SandboxStatus.RUNNING
        assert agent_server.init_get_urls == [f'{AGENT_SERVER_URL}/api/init'] * 3


class TestInitHandshake:
    @pytest.mark.asyncio
    async def test_init_body_is_accepted_by_the_agent_server(self, k8s, db_session):
        agent_server = FakeAgentServer()

        sandbox = await _service(
            db_session, k8s, httpx_client=agent_server
        ).start_sandbox()

        body = agent_server.init_post_bodies[0]
        # extra='forbid' on the real model: validating proves the body carries
        # only fields the agent server accepts.
        InitRequest.model_validate(body)
        assert body['session_api_keys'] == [sandbox.session_api_key]
        assert body['conversations_path'] == '/workspace/conversations'
        assert body['bash_events_dir'] == '/workspace/bash_events'
        assert body['conversation_worktree_root'] == '/workspace/worktrees'
        assert body['webhooks'] == [{'base_url': f'{WEB_URL}/api/v1/webhooks'}]
        assert body['allow_cors_origins'] == [WEB_URL]
        assert agent_server.init_post_urls == [f'{AGENT_SERVER_URL}/api/init']
        assert agent_server.init_post_headers[0] == {'X-Init-API-Key': INIT_API_KEY}

    @pytest.mark.asyncio
    async def test_secret_key_is_the_session_key(self, k8s, db_session):
        """Per sandbox, never the template's shared key, and stable across resume."""
        agent_server = FakeAgentServer()

        sandbox = await _service(
            db_session, k8s, httpx_client=agent_server
        ).start_sandbox()

        secret_key = agent_server.init_post_bodies[0]['secret_key']
        assert secret_key == sandbox.session_api_key
        assert secret_key != INIT_API_KEY

    @pytest.mark.asyncio
    async def test_env_carries_worker_ports_and_agent_server_env(
        self, k8s, monkeypatch, db_session
    ):
        monkeypatch.setenv('LLM_API_KEY', 'sk-secret')
        agent_server = FakeAgentServer()

        await _service(db_session, k8s, httpx_client=agent_server).start_sandbox()

        env = agent_server.init_post_bodies[0]['env']
        assert env[WORKER_1] == str(WORKER_1_PORT)
        assert env[WORKER_2] == str(WORKER_2_PORT)
        assert env['LLM_API_KEY'] == 'sk-secret'

    @pytest.mark.asyncio
    async def test_webhook_base_url_overrides_the_web_url(self, k8s, db_session):
        agent_server = FakeAgentServer()

        await _service(
            db_session,
            k8s,
            httpx_client=agent_server,
            webhook_base_url='http://openhands.openhands.svc.cluster.local:3000/',
        ).start_sandbox()

        assert agent_server.init_post_bodies[0]['webhooks'] == [
            {
                'base_url': 'http://openhands.openhands.svc.cluster.local:3000/api/v1/webhooks'
            }
        ]

    @pytest.mark.asyncio
    async def test_no_webhook_without_a_reachable_url(self, k8s, db_session):
        agent_server = FakeAgentServer()

        with patch.object(k8s_agent_sandbox_service._logger, 'warning') as warning:
            await _service(
                db_session,
                k8s,
                httpx_client=agent_server,
                web_url='http://localhost:3000',
            ).start_sandbox()

        assert 'webhooks' not in agent_server.init_post_bodies[0]
        warning.assert_called_once()


# ---------------------------------------------------------------------------
# Status and reads
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('ready', 'sandbox_name', 'expected'),
        [
            (READY, SANDBOX_NAME, SandboxStatus.RUNNING),
            (READY, None, SandboxStatus.STARTING),
            (BOOTING, SANDBOX_NAME, SandboxStatus.STARTING),
            (None, None, SandboxStatus.STARTING),
            (SUSPENDED, SANDBOX_NAME, SandboxStatus.PAUSED),
            (
                {'type': 'Ready', 'status': 'False', 'reason': 'PodFailed'},
                SANDBOX_NAME,
                SandboxStatus.ERROR,
            ),
        ],
    )
    async def test_maps_the_ready_condition(
        self, k8s, db_session, store, ready, sandbox_name, expected
    ):
        await store(_stored())
        k8s.add_claim(ready=ready, sandbox_name=sandbox_name)

        sandbox = await _service(db_session, k8s).get_sandbox(CLAIM_NAME)

        assert sandbox.status == expected
        if expected == SandboxStatus.RUNNING:
            assert sandbox.session_api_key == SESSION_API_KEY
            assert sandbox.exposed_urls
        else:
            assert sandbox.session_api_key is None
            assert sandbox.exposed_urls is None

    @pytest.mark.asyncio
    async def test_error_carries_the_reason(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim(
            ready={
                'type': 'Ready',
                'status': 'False',
                'reason': 'SandboxExpired',
                'message': 'expired',
            }
        )

        sandbox = await _service(db_session, k8s).get_sandbox(CLAIM_NAME)

        assert sandbox.status_detail == 'SandboxExpired: expired'

    @pytest.mark.asyncio
    async def test_row_without_a_claim_is_missing(self, k8s, db_session, store):
        await store(_stored())

        sandbox = await _service(db_session, k8s).get_sandbox(CLAIM_NAME)

        assert sandbox.status == SandboxStatus.MISSING
        assert sandbox.session_api_key is None

    @pytest.mark.asyncio
    async def test_claim_being_deleted_is_missing(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()['metadata']['deletionTimestamp'] = '2026-01-01T00:00:00Z'

        sandbox = await _service(db_session, k8s).get_sandbox(CLAIM_NAME)

        assert sandbox.status == SandboxStatus.MISSING


class TestUserScoping:
    @pytest.mark.asyncio
    async def test_other_user_cannot_see_the_sandbox(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()

        service = _service(db_session, k8s, user_id=OTHER_USER_ID)

        assert await service.get_sandbox(CLAIM_NAME) is None
        assert (await service.search_sandboxes()).items == []
        assert await service.pause_sandbox(CLAIM_NAME) is False
        assert await service.resume_sandbox(CLAIM_NAME) is False
        assert await service.delete_sandbox(CLAIM_NAME) is False
        assert k8s.modes == []
        assert k8s.deleted == []

    @pytest.mark.asyncio
    async def test_search_lists_only_the_callers_claims(self, k8s, db_session, store):
        await store(_stored(), _stored('oh-other', created_by_user_id=OTHER_USER_ID))
        k8s.add_claim()
        k8s.add_claim('oh-other', owner=OTHER_USER_ID, sandbox_name='other-sandbox')

        page = await _service(db_session, k8s).search_sandboxes()

        assert [sandbox.id for sandbox in page.items] == [CLAIM_NAME]
        assert page.items[0].status == SandboxStatus.RUNNING
        assert k8s.selectors == [
            f'{MANAGED_LABEL}=true,{OWNER_LABEL}={owner_label_value(OWNER_ID)}'
        ]

    @pytest.mark.asyncio
    async def test_admin_lists_every_owner(self, k8s, db_session, store):
        await store(_stored(), _stored('oh-other', created_by_user_id=OTHER_USER_ID))
        k8s.add_claim()
        k8s.add_claim('oh-other', owner=OTHER_USER_ID, sandbox_name='other-sandbox')

        page = await _service(db_session, k8s, user_context=ADMIN).search_sandboxes()

        assert {sandbox.id for sandbox in page.items} == {CLAIM_NAME, 'oh-other'}
        assert {sandbox.status for sandbox in page.items} == {SandboxStatus.RUNNING}
        assert k8s.selectors == [f'{MANAGED_LABEL}=true']

    @pytest.mark.asyncio
    async def test_search_without_rows_skips_the_api(self, k8s, db_session):
        page = await _service(db_session, k8s).search_sandboxes()

        assert page.items == []
        assert k8s.selectors == []

    @pytest.mark.asyncio
    async def test_session_key_lookup(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()
        service = _service(db_session, k8s, user_context=ADMIN)

        record = await service.get_sandbox_record_by_session_api_key(SESSION_API_KEY)
        sandbox = await service.get_sandbox_by_session_api_key(SESSION_API_KEY)

        assert record.id == CLAIM_NAME
        assert record.created_by_user_id == OWNER_ID
        assert sandbox.status == SandboxStatus.RUNNING
        assert await service.get_sandbox_record_by_session_api_key('nope') is None


class TestVSCodeUrl:
    @pytest.mark.asyncio
    async def test_built_from_the_router_path_and_the_session_key(
        self, k8s, db_session, store
    ):
        await store(_stored())
        k8s.add_claim()
        httpx_client = AsyncMock()

        sandbox = await _service(
            db_session, k8s, httpx_client=httpx_client
        ).get_sandbox(CLAIM_NAME)

        urls = {url.name: url for url in sandbox.exposed_urls}
        assert urls[VSCODE].url == VSCODE_URL
        assert urls[VSCODE].port == 8001
        httpx_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_includes_it(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()
        httpx_client = AsyncMock()

        page = await _service(
            db_session, k8s, httpx_client=httpx_client
        ).search_sandboxes()

        urls = {url.name: url.url for url in page.items[0].exposed_urls}
        assert urls[VSCODE] == VSCODE_URL
        httpx_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Pause, resume, delete
# ---------------------------------------------------------------------------


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_suspends_the_sandbox(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()
        service = _service(db_session, k8s)

        assert await service.pause_sandbox(CLAIM_NAME) is True

        assert k8s.modes == [(SANDBOX_NAME, 'Suspended')]
        sandbox = await service.get_sandbox(CLAIM_NAME)
        assert sandbox.status == SandboxStatus.PAUSED

    @pytest.mark.asyncio
    async def test_pause_of_a_missing_claim(self, k8s, db_session, store):
        await store(_stored())

        assert await _service(db_session, k8s).pause_sandbox(CLAIM_NAME) is False

    @pytest.mark.asyncio
    async def test_pause_before_a_pod_is_bound(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim(ready=BOOTING, sandbox_name=None)

        with pytest.raises(SandboxError, match='no pod yet'):
            await _service(db_session, k8s).pause_sandbox(CLAIM_NAME)

    @pytest.mark.asyncio
    async def test_resume_reinitializes_with_the_same_key(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim(ready=SUSPENDED)
        agent_server = FakeAgentServer()
        service = _service(db_session, k8s, httpx_client=agent_server)

        assert await service.resume_sandbox(CLAIM_NAME) is True

        assert k8s.modes == [(SANDBOX_NAME, 'Running')]
        body = agent_server.init_post_bodies[0]
        assert body['session_api_keys'] == [SESSION_API_KEY]
        assert body['secret_key'] == SESSION_API_KEY
        sandbox = await service.get_sandbox(CLAIM_NAME)
        assert sandbox.status == SandboxStatus.RUNNING
        assert sandbox.session_api_key == SESSION_API_KEY

    @pytest.mark.asyncio
    async def test_resume_waits_for_the_new_generation(self, k8s, db_session, store):
        """A Ready condition from before the resume must not pass."""
        await store(_stored())
        k8s.add_claim(ready=SUSPENDED)
        stale = {
            'metadata': {'generation': 3},
            'status': {'conditions': [{**READY, 'observedGeneration': 2}]},
        }
        fresh = {
            'metadata': {'generation': 3},
            'status': {'conditions': [{**READY, 'observedGeneration': 3}]},
        }
        k8s.set_operating_mode = AsyncMock(return_value={'metadata': {'generation': 3}})
        k8s.get_sandbox = AsyncMock(side_effect=[stale, stale, fresh])

        assert await _service(db_session, k8s).resume_sandbox(CLAIM_NAME) is True

        assert k8s.get_sandbox.await_count == 3

    @pytest.mark.asyncio
    async def test_resume_leaves_an_initialized_server_alone(
        self, k8s, db_session, store
    ):
        await store(_stored())
        k8s.add_claim(ready=SUSPENDED)
        agent_server = FakeAgentServer(init_state='ready')

        assert (
            await _service(db_session, k8s, httpx_client=agent_server).resume_sandbox(
                CLAIM_NAME
            )
            is True
        )

        assert agent_server.init_post_bodies == []

    @pytest.mark.asyncio
    async def test_resume_of_a_running_sandbox_does_nothing(
        self, k8s, db_session, store
    ):
        await store(_stored())
        k8s.add_claim()
        service = _service(db_session, k8s)

        with patch.object(service, 'pause_old_sandboxes') as pause:
            assert await service.resume_sandbox(CLAIM_NAME) is True

        pause.assert_not_called()
        assert k8s.modes == []

    @pytest.mark.asyncio
    async def test_resume_of_a_missing_claim(self, k8s, db_session, store):
        await store(_stored())

        assert await _service(db_session, k8s).resume_sandbox(CLAIM_NAME) is False


class TestDelete:
    @pytest.mark.asyncio
    async def test_deletes_the_claim_and_the_row(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()

        assert await _service(db_session, k8s).delete_sandbox(CLAIM_NAME) is True

        assert k8s.deleted == [CLAIM_NAME]
        assert not await _row_exists(db_session, CLAIM_NAME)

    @pytest.mark.asyncio
    async def test_claim_already_gone_still_removes_the_row(
        self, k8s, db_session, store
    ):
        await store(_stored())

        assert await _service(db_session, k8s).delete_sandbox(CLAIM_NAME) is True

        assert not await _row_exists(db_session, CLAIM_NAME)

    @pytest.mark.asyncio
    async def test_failed_delete_keeps_the_row(self, k8s, db_session, store):
        await store(_stored())
        k8s.add_claim()
        k8s.delete_error = SandboxError('Could not delete a SandboxClaim: 500')

        with pytest.raises(SandboxDeleteRetryError):
            await _service(db_session, k8s).delete_sandbox(CLAIM_NAME)

        assert await _row_exists(db_session, CLAIM_NAME)


# ---------------------------------------------------------------------------
# Kubernetes API client
# ---------------------------------------------------------------------------


@pytest.fixture
def api():
    client = AgentSandboxClient(k8s_client.ApiClient(), NAMESPACE)
    client._api = MagicMock()
    return client


class TestAgentSandboxClient:
    @pytest.mark.asyncio
    async def test_missing_object_reads_as_none(self, api):
        api._api.get_namespaced_custom_object.side_effect = ApiException(status=404)

        assert await api.get_claim('gone') is None
        assert await api.get_sandbox('gone') is None

    @pytest.mark.asyncio
    async def test_delete_of_a_missing_claim_is_false(self, api):
        api._api.delete_namespaced_custom_object.side_effect = ApiException(status=404)

        assert await api.delete_claim('gone') is False

    @pytest.mark.asyncio
    async def test_forbidden_names_the_permissions(self, api):
        api._api.create_namespaced_custom_object.side_effect = ApiException(
            status=403, reason='Forbidden'
        )

        with pytest.raises(SandboxError, match='create, get, list and delete'):
            await api.create_claim({})

    @pytest.mark.asyncio
    async def test_missing_crd_names_the_install(self, api):
        api._api.list_namespaced_custom_object.side_effect = ApiException(status=404)

        with pytest.raises(SandboxError, match='agent-sandbox is installed'):
            await api.list_claims(f'{MANAGED_LABEL}=true')

    @pytest.mark.asyncio
    async def test_unreachable_api(self, api):
        api._api.get_namespaced_custom_object.side_effect = (
            urllib3.exceptions.MaxRetryError(None, '/', 'connection refused')
        )

        with pytest.raises(SandboxError, match='unreachable'):
            await api.get_claim('any')

    @pytest.mark.asyncio
    async def test_operating_mode_is_a_merge_patch_on_the_sandbox(self, api):
        api._api.patch_namespaced_custom_object.return_value = {'metadata': {}}

        await api.set_operating_mode(SANDBOX_NAME, 'Suspended')

        args = api._api.patch_namespaced_custom_object.call_args.args
        assert args == (
            'agents.x-k8s.io',
            'v1beta1',
            NAMESPACE,
            'sandboxes',
            SANDBOX_NAME,
            {'spec': {'operatingMode': 'Suspended'}},
        )
