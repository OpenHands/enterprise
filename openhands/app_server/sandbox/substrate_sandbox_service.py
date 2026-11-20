import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from posixpath import dirname
from typing import Any, AsyncGenerator, ClassVar

import base62
import httpx
from fastapi import Request
from google.protobuf import json_format
from pydantic import Field, SecretStr

from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
from openhands.app_server.sandbox.managed_sandbox_service import (
    PROBE_TIMEOUT_SECONDS,
    ManagedSandboxService,
    ManagedSandboxServiceInjector,
    ProviderOutcome,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    WORKER_1,
    WORKER_2,
    ExposedUrl,
    SandboxInfo,
    SandboxPage,
    SandboxRecord,
    SandboxStartedBy,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    get_agent_server_env,
    resolve_sandbox_spec,
)
from openhands.app_server.sandbox.sandbox_store import (
    SUBSTRATE_BACKEND,
    StoredSandbox,
    get_credential_org_id,
    get_stored_sandbox_by_session_api_key,
    hash_session_api_key,
    require_user_id,
    sandbox_started_by,
    search_stored_sandboxes,
)
from openhands.app_server.sandbox.substrate import ateapi_pb2 as pb
from openhands.app_server.sandbox.substrate.control import SubstrateControl
from openhands.app_server.sandbox.substrate.ingress import (
    RouterSettings,
    RouterTransport,
    actor_url,
)
from openhands.app_server.sandbox.substrate_sandbox_spec_service import (
    SubstrateSandboxSpecInfo,
)
from openhands.app_server.services.injector import InjectorState

_logger = logging.getLogger(__name__)

# Browsers and the app reach a port on a sandbox at
# {proxy_url}{PROXY_PATH}/{sandbox_id}/{port}. See substrate_proxy_router.
PROXY_PATH = '/api/v1/sandbox-proxy'

WORKER_1_PORT = 8011
WORKER_2_PORT = 8012

ACTOR_NAME_PREFIX = 'oh-'

# Without an egress policy an actor reaches nothing, not even the LLM. This
# allows every host: TLS on 443, passed through, and cleartext HTTP on any
# other port. Substrate refuses two rules for the same host on all ports.
DEFAULT_EGRESS_RULES = [
    {'tlsPassthrough': {'hostnames': ['*'], 'ports': {'numbers': [443]}}},
    {'http': {'hostnames': ['*'], 'ports': {'all': {}}}},
]

_STATUSES = {
    pb.ACTOR_STATE_UNSPECIFIED: SandboxStatus.STARTING,
    pb.ACTOR_STATE_RESUMING: SandboxStatus.STARTING,
    pb.ACTOR_STATE_RUNNING: SandboxStatus.RUNNING,
    pb.ACTOR_STATE_SUSPENDING: SandboxStatus.PAUSED,
    pb.ACTOR_STATE_SUSPENDED: SandboxStatus.PAUSED,
    pb.ACTOR_STATE_REVERTING: SandboxStatus.PAUSED,
    pb.ACTOR_STATE_CRASHED: SandboxStatus.ERROR,
    pb.ACTOR_STATE_DELETING: SandboxStatus.MISSING,
}

MISSING_INIT_API_KEY = (
    'has no init API key. The ActorTemplate boots the agent server with a '
    'static OH_SECRET_KEY, and the app server must be configured with the same '
    'value: set SUBSTRATE_INIT_API_KEY.'
)
MISSING_PROXY_URL = (
    'Neither SUBSTRATE_PROXY_URL nor OH_WEB_URL is set. Browsers reach Substrate '
    "sandboxes through the app's own URL."
)


def actor_status(actor: pb.Actor | None) -> SandboxStatus:
    if actor is None:
        return SandboxStatus.MISSING
    return _STATUSES.get(actor.status.state, SandboxStatus.STARTING)


def parse_egress_rules(rules_json: str) -> list[pb.EgressRule]:
    """EgressRules from a JSON list in proto JSON form, as kubectl ate writes them."""
    return [
        json_format.ParseDict(rule, pb.EgressRule()) for rule in json.loads(rules_json)
    ]


def _as_substrate_spec(sandbox_spec: SandboxSpecInfo) -> SubstrateSandboxSpecInfo:
    """Narrow a spec to the Substrate shape, filling defaults for a plain spec."""
    if isinstance(sandbox_spec, SubstrateSandboxSpecInfo):
        return sandbox_spec
    return SubstrateSandboxSpecInfo(**sandbox_spec.model_dump())


def _init_api_key(sandbox_spec: SubstrateSandboxSpecInfo) -> str | None:
    """The template's init key, treating an empty value as unset."""
    if sandbox_spec.init_api_key is None:
        return None
    return sandbox_spec.init_api_key.get_secret_value() or None


@dataclass
class SubstrateSandboxService(ManagedSandboxService):
    """Sandbox service backed by Agent Substrate actors.

    The operator registers an ActorTemplate ahead of time. It runs the agent
    server in deferred-init mode, and its golden snapshot holds the booted,
    dormant server. ``start_sandbox`` creates an actor from it, gives the actor
    its egress policy, resumes it, and completes the ``/api/init`` handshake
    before returning. The template is shared by every actor, so everything per
    user reaches the agent server through that handshake.

    The actor's name is the sandbox id. Ownership, spec identity and the
    session API key live in the sandbox table (see ``sandbox_store``). Pause
    suspends the actor to a snapshot of its memory and files, and releases its
    worker. Resume restores it.

    Browsers and the app reach the agent server through the app's own proxy
    (``substrate_proxy_router``), which holds the client certificate that
    atenet-router requires. The service itself calls the actor through the
    router directly.
    """

    backend: ClassVar[str] = SUBSTRATE_BACKEND

    sandbox_spec_service: SandboxSpecService
    control: SubstrateControl
    router_client: httpx.AsyncClient
    proxy_url: str
    egress_rules: list[pb.EgressRule]
    resume_timeout_seconds: int
    init_timeout_seconds: int
    poll_interval: float
    web_url: str | None = None
    webhook_base_url: str | None = None
    permitted_cors_origins: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Info mapping
    # ------------------------------------------------------------------

    async def _get_spec(self, sandbox_spec_id: str) -> SubstrateSandboxSpecInfo:
        """Get the spec a sandbox was created from, or the default shape.

        A sandbox outlives an edit to the configured spec list, so a spec that
        is no longer offered still needs ports and a working dir.
        """
        sandbox_spec = await self.sandbox_spec_service.get_sandbox_spec(sandbox_spec_id)
        if sandbox_spec is None:
            return SubstrateSandboxSpecInfo(id=sandbox_spec_id, command=None)
        return _as_substrate_spec(sandbox_spec)

    def _port_url(self, sandbox_id: str, port: int) -> str:
        """The proxy URL of a port on a sandbox."""
        return f'{self.proxy_url.rstrip("/")}{PROXY_PATH}/{sandbox_id}/{port}'

    def _exposed_urls(
        self,
        sandbox_id: str,
        sandbox_spec: SubstrateSandboxSpecInfo,
        session_api_key: str,
    ) -> list[ExposedUrl]:
        """The agent server, worker and VSCode URLs."""
        # The agent server gives VSCode the session API key as its connection
        # token on POST /api/init.
        vscode_url = (
            f'{self._port_url(sandbox_id, sandbox_spec.vscode_port)}'
            f'/?tkn={session_api_key}&folder={sandbox_spec.working_dir}'
        )
        return [
            ExposedUrl(
                name=AGENT_SERVER,
                url=self._port_url(sandbox_id, sandbox_spec.agent_server_port),
                port=sandbox_spec.agent_server_port,
            ),
            ExposedUrl(
                name=WORKER_1,
                url=self._port_url(sandbox_id, WORKER_1_PORT),
                port=WORKER_1_PORT,
            ),
            ExposedUrl(
                name=WORKER_2,
                url=self._port_url(sandbox_id, WORKER_2_PORT),
                port=WORKER_2_PORT,
            ),
            ExposedUrl(name=VSCODE, url=vscode_url, port=sandbox_spec.vscode_port),
        ]

    async def _to_sandbox_info(
        self, stored_sandbox: StoredSandbox, actor: pb.Actor | None
    ) -> SandboxInfo:
        """Build a SandboxInfo from the stored row plus its actor.

        A row whose actor is gone is MISSING, which drives the
        archived-conversation UI.
        """
        status = actor_status(actor)
        session_api_key = self._raw_key(stored_sandbox)
        exposed_urls = None
        if status == SandboxStatus.RUNNING and session_api_key:
            sandbox_spec = await self._get_spec(stored_sandbox.sandbox_spec_id)
            exposed_urls = self._exposed_urls(
                stored_sandbox.id, sandbox_spec, session_api_key
            )
        else:
            session_api_key = None

        status_detail = None
        if actor is not None and status == SandboxStatus.ERROR:
            status_detail = actor.status.crash.message or 'The actor crashed'

        return SandboxInfo(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
            credential_org_id=stored_sandbox.credential_org_id,
            started_by=sandbox_started_by(stored_sandbox),
            sandbox_spec_id=stored_sandbox.sandbox_spec_id,
            status=status,
            session_api_key=session_api_key,
            exposed_urls=exposed_urls,
            created_at=stored_sandbox.created_at,
            status_detail=status_detail,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_key(stored_sandbox: StoredSandbox) -> str | None:
        """The session API key on a row, decrypted."""
        key = stored_sandbox.session_api_key
        return key.get_secret_value() if key else None

    async def search_sandboxes(
        self,
        page_id: str | None = None,
        limit: int = 100,
    ) -> SandboxPage:
        """Search for sandboxes: one query for the page, then each row's actor."""
        page = await search_stored_sandboxes(
            self.db_session, self.user_context, SUBSTRATE_BACKEND, page_id, limit
        )
        actors = await asyncio.gather(
            *(self.control.get_actor(stored.id) for stored in page.items)
        )
        sandboxes = [
            await self._to_sandbox_info(stored_sandbox, actor)
            for stored_sandbox, actor in zip(page.items, actors, strict=True)
        ]
        return SandboxPage(items=sandboxes, next_page_id=page.next_page_id)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return None
        return await self._to_sandbox_info(
            stored_sandbox, await self.control.get_actor(sandbox_id)
        )

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        """Get a single sandbox by session API key, on the hash index."""
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, SUBSTRATE_BACKEND, session_api_key
        )
        if stored_sandbox is None:
            return None
        return await self._to_sandbox_info(
            stored_sandbox, await self.control.get_actor(stored_sandbox.id)
        )

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        """Get sandbox identity by session API key, with no Substrate call.

        This runs on the webhook path, once per batch of agent events.
        """
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, SUBSTRATE_BACKEND, session_api_key
        )
        if stored_sandbox is None:
            return None
        return SandboxRecord(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
            credential_org_id=stored_sandbox.credential_org_id,
        )

    # ------------------------------------------------------------------
    # Agent server probes
    # ------------------------------------------------------------------

    async def _agent_server_get(
        self, sandbox: SandboxInfo, path: str, timeout: float
    ) -> httpx.Response:
        """GET a path on the agent server, through the router.

        The probes skip the app's proxy, which only serves callers holding the
        session key or the owner's login.
        """
        sandbox_spec = await self._get_spec(sandbox.sandbox_spec_id)
        return await self.router_client.get(
            actor_url(sandbox.id, sandbox_spec.agent_server_port, path),
            timeout=timeout,
        )

    async def _check_agent_server_alive(
        self, sandbox: SandboxInfo, httpx_client: httpx.AsyncClient
    ) -> bool:
        try:
            response = await self._agent_server_get(sandbox, '/alive', 5.0)
            return response.is_success
        except httpx.HTTPError as exc:
            _logger.debug(f'Agent server health check failed for {sandbox.id}: {exc}')
            return False

    async def get_idle_time(self, sandbox: SandboxInfo) -> float | None:
        try:
            response = await self._agent_server_get(
                sandbox, '/server_info', PROBE_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return float(response.json()['idle_time'])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            _logger.info(
                'sandbox_lifecycle.probe_failed',
                extra={'sandbox_id': sandbox.id, 'error': str(exc)},
            )
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_sandbox(
        self,
        sandbox_spec_id: str | None = None,
        sandbox_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        started_by: SandboxStartedBy = SandboxStartedBy.SANDBOX_API,
    ) -> SandboxInfo:
        """Create an actor and initialize its agent server.

        ``sandbox_id`` is ignored. The id is the actor's name, which must be a
        DNS label, so the app picks it.

        The ``/api/init`` handshake completes before this returns. A dormant
        agent server answers ``/alive``, ``/health`` and ``/ready`` with 200,
        so a caller waiting on readiness would otherwise be handed a server
        that rejects every ``/api`` call with a 503.
        """
        # Every sandbox has an owner. Check before pause_old_sandboxes, which
        # would reach every user's sandboxes as ADMIN.
        user_id = await require_user_id(self.user_context)
        credential_org_id = await get_credential_org_id(self.user_context)
        if not self.proxy_url:
            raise SandboxError(MISSING_PROXY_URL)

        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        user_default_spec_id = await self.user_context.get_default_sandbox_spec_id()
        sandbox_spec = _as_substrate_spec(
            await resolve_sandbox_spec(
                sandbox_spec_id,
                user_default_spec_id,
                self.sandbox_spec_service,
                _logger,
            )
        )
        # Checked before create: without the key the handshake cannot succeed.
        if _init_api_key(sandbox_spec) is None:
            raise SandboxError(
                f'Sandbox spec {sandbox_spec.id!r} {MISSING_INIT_API_KEY}'
            )

        actor_name = f'{ACTOR_NAME_PREFIX}{secrets.token_hex(12)}'
        session_api_key = base62.encodebytes(os.urandom(32))
        stored_sandbox = self._new_stored_sandbox(
            id=actor_name,
            created_by_user_id=user_id,
            credential_org_id=credential_org_id,
            started_by=started_by.value,
            sandbox_spec_id=sandbox_spec.id,
            session_api_key_hash=hash_session_api_key(session_api_key),
            session_api_key=SecretStr(session_api_key),
        )
        await self.control.create_actor(actor_name, sandbox_spec.id)
        try:
            self.db_session.add(stored_sandbox)
            await self.db_session.flush()
            # Substrate does not give an actor its template's golden policy,
            # so the policy has to exist before the actor first runs.
            await self.control.create_egress_policy(actor_name, self.egress_rules)
            if (
                await self.control.resume_actor(actor_name, self.resume_timeout_seconds)
                is None
            ):
                raise SandboxError(f'Actor {actor_name} was deleted while starting')
            await self._initialize_agent_server(
                actor_name, sandbox_spec, session_api_key, extra_env=extra_env
            )
        except BaseException:
            # Init is never retried: an actor that took a half-finished init
            # is not worth reasoning about when a new one is a snapshot away.
            await self._delete_actor_quietly(actor_name)
            raise

        return SandboxInfo(
            id=actor_name,
            created_by_user_id=user_id,
            credential_org_id=credential_org_id,
            started_by=started_by,
            sandbox_spec_id=sandbox_spec.id,
            status=SandboxStatus.RUNNING,
            session_api_key=session_api_key,
            exposed_urls=self._exposed_urls(actor_name, sandbox_spec, session_api_key),
            created_at=stored_sandbox.created_at,
        )

    async def _delete_actor_quietly(self, actor_name: str) -> None:
        """Delete an actor on a failure path, without masking the original error."""
        try:
            await self.control.delete_actor(actor_name)
        except Exception:
            _logger.warning(f'Could not delete actor {actor_name} after a failed start')

    async def _initialize_agent_server(
        self,
        actor_name: str,
        sandbox_spec: SubstrateSandboxSpecInfo,
        session_api_key: str,
        allow_initialized: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        """Wait for the dormant agent server and hand it its runtime config.

        With ``allow_initialized``, an agent server that is already
        initialized is left alone. That is the usual case on resume, which
        restores the server's memory.
        """
        agent_server_url = actor_url(actor_name, sandbox_spec.agent_server_port, '')
        if not await self._wait_for_dormant(agent_server_url, allow_initialized):
            return

        init_api_key = _init_api_key(sandbox_spec)
        headers = {'X-Init-API-Key': init_api_key} if init_api_key else {}
        try:
            response = await self.router_client.post(
                f'{agent_server_url}/api/init',
                json=self._build_init_request(sandbox_spec, session_api_key, extra_env),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise SandboxError(
                f'Could not reach the agent server in actor {actor_name}'
            ) from exc
        if response.status_code != 200:
            _logger.error(
                f'Agent server init for actor {actor_name} returned '
                f'{response.status_code}: {response.text}'
            )
            if response.status_code == 401:
                raise SandboxError(
                    f'The agent server in actor {actor_name} rejected the init API '
                    'key. It must match the OH_SECRET_KEY in ActorTemplate '
                    f'{sandbox_spec.id!r}.'
                )
            raise SandboxError(f'Failed to initialize actor {actor_name}')

    async def _wait_for_dormant(
        self, agent_server_url: str, allow_initialized: bool
    ) -> bool:
        """Poll ``GET /api/init`` until the agent server can be claimed.

        Returns True when it is dormant, and False when it is already
        initialized and ``allow_initialized`` is set.
        """
        deadline = time.monotonic() + self.init_timeout_seconds
        while True:
            try:
                response = await self.router_client.get(f'{agent_server_url}/api/init')
                if response.status_code == 200:
                    state = response.json().get('state')
                    if state == 'dormant':
                        return True
                    if state == 'ready':
                        if allow_initialized:
                            return False
                        raise SandboxError(
                            f'Agent server at {agent_server_url} is already initialized'
                        )
            except (httpx.HTTPError, ValueError) as exc:
                _logger.debug(f'Waiting for {agent_server_url}: {exc}')
            if time.monotonic() >= deadline:
                raise SandboxError(
                    f'Agent server at {agent_server_url} did not become ready '
                    f'within {self.init_timeout_seconds}s'
                )
            await asyncio.sleep(self.poll_interval)

    def _build_init_request(
        self,
        sandbox_spec: SubstrateSandboxSpecInfo,
        session_api_key: str,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the ``POST /api/init`` body.

        The agent server forbids extra fields, so this carries only what it
        accepts.
        """
        # The spec's working dir is the project checkout; its parent is the
        # workspace root.
        working_dir = sandbox_spec.working_dir.rstrip('/')
        workspace_dir = dirname(working_dir)
        if workspace_dir in ('', '/'):
            workspace_dir = working_dir
        state_dir = sandbox_spec.state_dir.rstrip('/')
        body: dict[str, Any] = {
            'session_api_keys': [session_api_key],
            # What the agent server uses when it has no secret key of its own.
            # Sent because the template boots it holding the shared init key.
            # It lives as long as the session key, so persisted secrets still
            # decrypt after a cold boot.
            'secret_key': session_api_key,
            'conversations_path': f'{state_dir}/conversations',
            'bash_events_dir': f'{state_dir}/bash_events',
            'conversation_worktree_root': f'{workspace_dir}/worktrees',
            # `get_agent_server_env` is resolved here rather than baked into
            # the spec's initial_env, because it can carry LLM_API_KEY and the
            # spec is serialized verbatim by the public sandbox-specs endpoint.
            'env': {
                WORKER_1: str(WORKER_1_PORT),
                WORKER_2: str(WORKER_2_PORT),
                **sandbox_spec.initial_env,
                **get_agent_server_env(),
            },
        }
        body['env'].update(self._lifecycle_env())
        if extra_env:
            body['env'].update(extra_env)
        cors_origins = []
        if self.web_url:
            cors_origins.append(self.web_url)
        cors_origins.extend(self.permitted_cors_origins)
        if cors_origins:
            body['allow_cors_origins'] = list(dict.fromkeys(cors_origins))

        webhook_base_url = self.webhook_base_url or self.web_url
        if webhook_base_url and 'localhost' not in webhook_base_url:
            body['webhooks'] = [
                {'base_url': f'{webhook_base_url.rstrip("/")}/api/v1/webhooks'}
            ]
        else:
            _logger.warning(
                'Neither SUBSTRATE_WEBHOOK_BASE_URL nor a non-localhost OH_WEB_URL '
                'is set, so sandboxes cannot post events back to the app server. '
                'Conversations will start but will not receive agent events.'
            )
        return body

    async def _resume_at_provider(
        self, stored_sandbox: StoredSandbox
    ) -> ProviderOutcome:
        """Resume the actor, then initialize its agent server if it cold-booted.

        A resume restores the agent server's memory, so it is usually already
        initialized. A snapshot without memory boots it dormant again, and it
        gets the key already on the row.
        """
        actor = await self.control.get_actor(stored_sandbox.id)
        status = actor_status(actor)
        if status == SandboxStatus.MISSING:
            return ProviderOutcome.FAILED
        if status == SandboxStatus.RUNNING:
            return ProviderOutcome.ALREADY_DONE
        if status != SandboxStatus.PAUSED:
            return ProviderOutcome.SKIPPED

        session_api_key = self._raw_key(stored_sandbox)
        if session_api_key is None:
            return ProviderOutcome.FAILED
        if (
            await self.control.resume_actor(
                stored_sandbox.id, self.resume_timeout_seconds
            )
            is None
        ):
            return ProviderOutcome.FAILED
        await self._initialize_agent_server(
            stored_sandbox.id,
            await self._get_spec(stored_sandbox.sandbox_spec_id),
            session_api_key,
            allow_initialized=True,
        )
        return ProviderOutcome.CHANGED

    async def _pause_at_provider(
        self, stored_sandbox: StoredSandbox
    ) -> ProviderOutcome:
        """Suspend the actor to a snapshot and release its worker."""
        actor = await self.control.get_actor(stored_sandbox.id)
        status = actor_status(actor)
        if status == SandboxStatus.MISSING:
            return ProviderOutcome.FAILED
        if status == SandboxStatus.PAUSED:
            return ProviderOutcome.ALREADY_DONE
        if status != SandboxStatus.RUNNING:
            return ProviderOutcome.SKIPPED
        if await self.control.suspend_actor(stored_sandbox.id) is None:
            return ProviderOutcome.FAILED
        return ProviderOutcome.CHANGED

    async def _delete_at_provider(self, stored_sandbox: StoredSandbox) -> None:
        try:
            await self.control.delete_actor(stored_sandbox.id)
        except SandboxError as exc:
            _logger.exception(
                f'Error deleting sandbox {stored_sandbox.id}', stack_info=True
            )
            raise SandboxDeleteRetryError(
                f'Could not complete delete for sandbox {stored_sandbox.id}: {exc}'
            ) from exc


class SubstrateSandboxServiceInjector(ManagedSandboxServiceInjector):
    """Dependency injector for Substrate sandbox services."""

    backend: ClassVar[str] = SUBSTRATE_BACKEND

    atespace: str = Field(
        default_factory=lambda: os.getenv('SUBSTRATE_ATESPACE', 'openhands'),
        description='Atespace the app creates actors in. SUBSTRATE_ATESPACE.',
    )
    api_endpoint: str = Field(
        default_factory=lambda: os.getenv(
            'SUBSTRATE_API_ENDPOINT', 'dns:///api.ate-system.svc:443'
        ),
        description='gRPC target of ate-api-server. SUBSTRATE_API_ENDPOINT.',
    )
    api_server_name: str | None = Field(
        default_factory=lambda: os.getenv('SUBSTRATE_API_SERVER_NAME') or None,
        description=(
            "Name to check ate-api-server's certificate against when the endpoint "
            'is not that name, such as a port-forward. SUBSTRATE_API_SERVER_NAME.'
        ),
    )
    api_ca_file: str | None = Field(
        default_factory=lambda: os.getenv(
            'SUBSTRATE_API_CA_FILE', '/var/run/ate/ca.crt'
        ),
        description=(
            "CA bundle for ate-api-server's certificate: the "
            'servicedns.podcert.ate.dev/identity trust bundle. SUBSTRATE_API_CA_FILE.'
        ),
    )
    api_token_file: str | None = Field(
        default_factory=lambda: os.getenv(
            'SUBSTRATE_API_TOKEN_FILE', '/var/run/ate/token'
        ),
        description=(
            'ServiceAccount token with audience api.ate-system.svc, read on every '
            'call. SUBSTRATE_API_TOKEN_FILE.'
        ),
    )
    router_address: str = Field(
        default_factory=lambda: os.getenv(
            'SUBSTRATE_ROUTER_ADDRESS', 'atenet-router.ate-system.svc:8444'
        ),
        description=(
            "host:port of atenet-router's CONNECT listener. SUBSTRATE_ROUTER_ADDRESS."
        ),
    )
    router_server_name: str | None = Field(
        default_factory=lambda: os.getenv('SUBSTRATE_ROUTER_SERVER_NAME') or None,
        description=(
            "Name to check the router's certificate against when the address is "
            'not that name. SUBSTRATE_ROUTER_SERVER_NAME.'
        ),
    )
    router_ca_file: str | None = Field(
        default_factory=lambda: os.getenv(
            'SUBSTRATE_ROUTER_CA_FILE', '/var/run/ate-router/router-ca.pem'
        ),
        description=(
            "CA bundle for the router's certificate: the "
            'servicedns.podcert.ate.dev/identity trust bundle. '
            'SUBSTRATE_ROUTER_CA_FILE.'
        ),
    )
    router_cert_file: str = Field(
        default_factory=lambda: os.getenv(
            'SUBSTRATE_ROUTER_CERT_FILE',
            '/var/run/ate-router/credential-bundle.pem',
        ),
        description=(
            "The app's client certificate chain for the router, PEM, with the key "
            'in the same file unless SUBSTRATE_ROUTER_KEY_FILE is set. Its SPIFFE '
            "ID must be in the router's --ingress-allowed-spiffe-ids. "
            'SUBSTRATE_ROUTER_CERT_FILE.'
        ),
    )
    router_key_file: str | None = Field(
        default_factory=lambda: os.getenv('SUBSTRATE_ROUTER_KEY_FILE') or None,
        description='Key for the client certificate. SUBSTRATE_ROUTER_KEY_FILE.',
    )
    proxy_url: str | None = Field(
        default_factory=lambda: os.getenv('SUBSTRATE_PROXY_URL') or None,
        description=(
            'Public URL of the app that browsers reach sandboxes through. '
            'Defaults to OH_WEB_URL. SUBSTRATE_PROXY_URL.'
        ),
    )
    webhook_base_url: str | None = Field(
        default_factory=lambda: os.getenv('SUBSTRATE_WEBHOOK_BASE_URL') or None,
        description=(
            'The app URL actors post events to, when it differs from OH_WEB_URL, '
            'such as an in-cluster Service URL. SUBSTRATE_WEBHOOK_BASE_URL.'
        ),
    )
    egress_rules: str = Field(
        default_factory=lambda: (
            os.getenv('SUBSTRATE_EGRESS_RULES') or json.dumps(DEFAULT_EGRESS_RULES)
        ),
        description=(
            "Every actor's EgressPolicy rules, as a JSON list in proto JSON. "
            'Defaults to every host: TLS on 443 and cleartext HTTP on any other '
            'port. SUBSTRATE_EGRESS_RULES.'
        ),
    )
    max_num_sandboxes: int = Field(
        default=10,
        description='Maximum number of sandboxes allowed to run simultaneously',
    )
    resume_timeout_seconds: int = Field(
        default=300,
        description=(
            'The max time to wait for an actor to resume, including time waiting '
            'for a free worker.'
        ),
    )
    init_timeout_seconds: int = Field(
        default=120,
        description=(
            'The max time to wait for a resumed actor to reach the dormant state '
            'before its start is considered failed.'
        ),
    )
    poll_interval: float = Field(
        default=1.0,
        description='Seconds between polls of a starting agent server',
    )

    def router_settings(self) -> RouterSettings:
        return RouterSettings(
            address=self.router_address,
            atespace=self.atespace,
            cert_file=self.router_cert_file,
            key_file=self.router_key_file,
            ca_file=self.router_ca_file,
            server_name=self.router_server_name,
        )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxService, None]:
        # Define inline to prevent circular lookup
        from openhands.app_server.config import (
            get_db_session,
            get_global_config,
            get_httpx_client,
            get_sandbox_spec_service,
            get_user_context,
        )

        config = get_global_config()
        control = SubstrateControl.connect(
            self.api_endpoint,
            self.atespace,
            ca_file=self.api_ca_file,
            server_name=self.api_server_name,
            token_file=self.api_token_file,
        )
        router_client = httpx.AsyncClient(
            transport=RouterTransport(self.router_settings()), timeout=30.0
        )
        try:
            async with (
                get_user_context(state, request) as user_context,
                get_httpx_client(state, request) as httpx_client,
                get_sandbox_spec_service(state, request) as sandbox_spec_service,
                get_db_session(state, request) as db_session,
            ):
                yield SubstrateSandboxService(
                    sandbox_spec_service=sandbox_spec_service,
                    user_context=user_context,
                    httpx_client=httpx_client,
                    db_session=db_session,
                    control=control,
                    router_client=router_client,
                    proxy_url=self.proxy_url or config.web_url or '',
                    egress_rules=parse_egress_rules(self.egress_rules),
                    max_num_sandboxes=self.max_num_sandboxes,
                    resume_timeout_seconds=self.resume_timeout_seconds,
                    init_timeout_seconds=self.init_timeout_seconds,
                    poll_interval=self.poll_interval,
                    web_url=config.web_url,
                    webhook_base_url=self.webhook_base_url,
                    permitted_cors_origins=config.permitted_cors_origins,
                    lifecycle=self.lifecycle,
                )
        finally:
            await router_client.aclose()
            await control.close()
