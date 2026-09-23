import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from posixpath import dirname
from typing import Any, AsyncGenerator

import aiohttp
import base62
import httpx
from fastapi import Request
from k8s_agent_sandbox import AsyncSandboxClient
from k8s_agent_sandbox.constants import (
    SANDBOX_API_GROUP,
    SANDBOX_API_VERSION,
    SANDBOX_PLURAL_NAME,
    TERMINAL_CLAIM_READY_REASONS,
)
from k8s_agent_sandbox.exceptions import SandboxError as AgentSandboxError
from k8s_agent_sandbox.exceptions import SandboxWarmPoolNotFoundError
from k8s_agent_sandbox.models import SandboxInClusterConnectionConfig
from kubernetes_asyncio.client import ApiException
from kubernetes_asyncio.config import ConfigException
from pydantic import Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.utils import utc_now
from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
from openhands.app_server.sandbox.k8s_agent_sandbox_spec_service import (
    K8sAgentSandboxSpecInfo,
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
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_service import (
    SandboxService,
    SandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    get_agent_server_env,
    resolve_sandbox_spec,
)
from openhands.app_server.sandbox.sandbox_store import (
    K8S_AGENT_SANDBOX_BACKEND,
    StoredSandbox,
    get_stored_sandbox,
    get_stored_sandbox_by_session_api_key,
    hash_session_api_key,
    require_user_id,
    search_stored_sandboxes,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.user_context import UserContext

_logger = logging.getLogger(__name__)

# Ownership lives in the sandbox table (see `sandbox_store`). These tag the
# claims the app makes, so that one with no row can be found. The owner label
# is a hash because a label value cannot hold every user id.
MANAGED_LABEL = 'openhands.dev/managed'
OWNER_LABEL = 'openhands.dev/owner'

WORKER_1_PORT = 8011
WORKER_2_PORT = 8012

# A claim mirrors its sandbox's Ready condition. This is its reason while the
# sandbox is suspended.
SUSPENDED_REASON = 'SandboxSuspended'
# Ready reasons that do not clear on their own. Any other not-ready reason is
# a claim still being served: adoption, a cold start, a booting pod.
FAILED_REASONS = TERMINAL_CLAIM_READY_REASONS | {
    'TemplateNotFound',
    'WarmPoolNotFound',
    'PodFailed',
    'PodSucceeded',
}

MISSING_INIT_API_KEY = (
    "has no init API key. The pool's SandboxTemplate boots the agent server "
    'with a static OH_SECRET_KEY, and the app server must be configured with '
    'the same value: set AGENT_SANDBOX_INIT_API_KEY (or '
    'OH_SANDBOX_SPEC_SPECS_0_INIT_API_KEY).'
)
MISSING_ROUTER_URL = (
    'AGENT_SANDBOX_ROUTER_URL is not set. It is the public URL of the '
    "agent-sandbox router's path-routing prefix, for example "
    'https://openhands.example.com/sandbox-router.'
)


def owner_label_value(user_id: str) -> str:
    """The OWNER_LABEL value for a user."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:32]


def _condition(obj: dict, condition_type: str = 'Ready') -> dict:
    """A status condition of a claim or sandbox, or an empty dict."""
    for condition in (obj.get('status') or {}).get('conditions') or []:
        if condition.get('type') == condition_type:
            return condition
    return {}


def _sandbox_name(claim: dict) -> str | None:
    """The Sandbox a claim holds. A warm one keeps its pool-generated name."""
    return ((claim.get('status') or {}).get('sandbox') or {}).get('name') or None


def _claim_status(claim: dict | None) -> SandboxStatus:
    if claim is None or claim['metadata'].get('deletionTimestamp'):
        return SandboxStatus.MISSING
    ready = _condition(claim)
    if ready.get('status') == 'True' and _sandbox_name(claim):
        return SandboxStatus.RUNNING
    reason = ready.get('reason')
    if reason == SUSPENDED_REASON:
        return SandboxStatus.PAUSED
    if reason in FAILED_REASONS:
        return SandboxStatus.ERROR
    return SandboxStatus.STARTING


def _describe(condition: dict) -> str:
    return f'{condition.get("reason")}: {condition.get("message")}'


def _as_k8s_spec(sandbox_spec: SandboxSpecInfo) -> K8sAgentSandboxSpecInfo:
    """Narrow a spec to the agent-sandbox shape, filling defaults for a plain spec."""
    if isinstance(sandbox_spec, K8sAgentSandboxSpecInfo):
        return sandbox_spec
    return K8sAgentSandboxSpecInfo(**sandbox_spec.model_dump())


def _init_api_key(sandbox_spec: K8sAgentSandboxSpecInfo) -> str | None:
    """The template's init key, treating an empty value as unset."""
    if sandbox_spec.init_api_key is None:
        return None
    return sandbox_spec.init_api_key.get_secret_value() or None


class AgentSandboxClient:
    """The agent-sandbox calls this backend makes, in one namespace.

    They go through agent-sandbox's Python SDK. A claim or sandbox that is gone
    reads as None. Every failure raises ``SandboxError``.
    """

    def __init__(self, namespace: str, sdk: AsyncSandboxClient | None = None):
        self.namespace = namespace
        # With cleanup on, the SDK deletes the sandboxes it created when the
        # process exits. The connection config is for the SDK's own runtime
        # API, which the agent server does not serve, so it goes unused.
        self._sdk = sdk or AsyncSandboxClient(
            connection_config=SandboxInClusterConnectionConfig(), cleanup=False
        )

    async def close(self) -> None:
        """Close the SDK's connections, and leave its sandboxes running.

        The SDK client's ``async with`` would delete them instead.
        """
        await self._sdk.close()

    @contextmanager
    def _reported(self, action: str) -> Iterator[None]:
        """Raise a failed call as a ``SandboxError`` that says what to fix."""
        try:
            yield
        except ApiException as exc:
            if exc.status in (401, 403):
                raise SandboxError(
                    f'Kubernetes refused to {action} in namespace '
                    f'{self.namespace!r} ({exc.status} {exc.reason}). The app '
                    'needs create, get, watch and delete on sandboxclaims, and '
                    'get, watch and patch on sandboxes, in that namespace.'
                ) from exc
            if exc.status == 404:
                raise SandboxError(
                    f'Could not {action}: 404 from the Kubernetes API. Check that '
                    'agent-sandbox is installed with its extensions and that '
                    f'namespace {self.namespace!r} exists.'
                ) from exc
            raise SandboxError(
                f'Could not {action}: {exc.status} {exc.reason}'
            ) from exc
        except ConfigException as exc:
            raise SandboxError(
                'No Kubernetes credentials: the app is not running in a pod and no '
                f'kubeconfig could be loaded ({exc})'
            ) from exc
        except aiohttp.ClientError as exc:
            raise SandboxError(
                f'Could not {action}: the Kubernetes API is unreachable ({exc})'
            ) from exc
        except (AgentSandboxError, TimeoutError) as exc:
            raise SandboxError(f'Could not {action}: {exc}') from exc

    async def claim(
        self, warm_pool: str, labels: dict[str, str], timeout: int
    ) -> tuple[str, str]:
        """Claim a ready sandbox from a warm pool.

        Returns the claim's name and its sandbox's. The SDK waits for the
        claim's Ready condition, and deletes a claim that fails or times out.
        """
        with self._reported(f'claim a sandbox from warm pool {warm_pool!r}'):
            try:
                sandbox = await self._sdk.create_sandbox(
                    warmpool=warm_pool,
                    namespace=self.namespace,
                    sandbox_ready_timeout=timeout,
                    labels=labels,
                )
            except SandboxWarmPoolNotFoundError as exc:
                raise SandboxError(
                    f'SandboxWarmPool {warm_pool!r} does not exist in namespace '
                    f'{self.namespace!r}. The operator creates it ahead of time, '
                    'and AGENT_SANDBOX_WARM_POOL names it.'
                ) from exc
        return sandbox.claim_name, sandbox.sandbox_id

    async def get_claim(self, name: str) -> dict | None:
        with self._reported('read a SandboxClaim'):
            return await self._sdk.k8s_helper.get_sandbox_claim(name, self.namespace)

    async def delete_claim(self, name: str) -> None:
        """Delete a claim, and with it its Sandbox, pod and volumes.

        A claim that is already gone is not an error.
        """
        with self._reported('delete a SandboxClaim'):
            await self._sdk.k8s_helper.delete_sandbox_claim(name, self.namespace)

    async def wait_for_sandbox(self, name: str, timeout: int) -> None:
        """Wait for a Sandbox's Ready condition."""
        with self._reported(f'wait for Sandbox {name}'):
            await self._sdk.k8s_helper.wait_for_sandbox_ready(
                name, self.namespace, timeout
            )

    async def set_operating_mode(self, name: str, mode: str) -> bool:
        """Set a Sandbox's operatingMode (Running or Suspended).

        Returns False when the Sandbox is gone. The SDK has no suspend or
        resume, so this patches the Sandbox with the SDK's Kubernetes client, as
        the SDK's GKE snapshot extension does.
        """
        helper = self._sdk.k8s_helper
        with self._reported('update a Sandbox'):
            # Reading it first also loads the client the patch goes through.
            if await helper.get_sandbox(name, self.namespace) is None:
                return False
            await helper.custom_objects_api.patch_namespaced_custom_object(
                group=SANDBOX_API_GROUP,
                version=SANDBOX_API_VERSION,
                namespace=self.namespace,
                plural=SANDBOX_PLURAL_NAME,
                name=name,
                body={'spec': {'operatingMode': mode}},
                _content_type='application/merge-patch+json',
            )
        return True


@dataclass
class K8sAgentSandboxService(SandboxService):
    """Sandbox service backed by kubernetes-sigs/agent-sandbox warm pools.

    The operator creates a SandboxTemplate and a SandboxWarmPool ahead of time.
    The pool keeps agent server pods booted in deferred-init mode, and
    ``start_sandbox`` claims one through agent-sandbox's Python SDK, then
    completes the ``/api/init`` handshake before returning. The pod booted
    before any user existed, so everything per user reaches it through that
    handshake.

    The claim's name is the sandbox id. Ownership, spec identity and the
    session API key live in the sandbox table (see ``sandbox_store``). Pause
    suspends the claimed Sandbox, which deletes its pod and keeps its volumes.
    Resume starts a new pod and repeats the handshake.

    Browsers and the app reach the agent server through agent-sandbox's router
    in path mode, at ``{router_url}/{namespace}/{sandbox}/{port}``. The router
    strips that path before forwarding. VSCode needs it anyway to write its own
    links, so the template passes it in as ``OH_VSCODE_BASE_PATH``.
    """

    sandbox_spec_service: SandboxSpecService
    user_context: UserContext
    httpx_client: httpx.AsyncClient
    db_session: AsyncSession
    k8s: AgentSandboxClient
    router_url: str
    max_num_sandboxes: int
    claim_timeout_seconds: int
    init_timeout_seconds: int
    poll_interval: float
    web_url: str | None = None
    webhook_base_url: str | None = None
    permitted_cors_origins: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    async def _get_stored_sandbox(self, sandbox_id: str) -> StoredSandbox | None:
        """Get a sandbox row, or None when the caller may not see it."""
        return await get_stored_sandbox(
            self.db_session, self.user_context, K8S_AGENT_SANDBOX_BACKEND, sandbox_id
        )

    # ------------------------------------------------------------------
    # Info mapping
    # ------------------------------------------------------------------

    async def _get_spec(self, sandbox_spec_id: str) -> K8sAgentSandboxSpecInfo:
        """Get the spec a sandbox was created from, or the default shape.

        A sandbox outlives an edit to the configured spec list, so a spec that
        is no longer offered still needs a port and a working dir.
        """
        sandbox_spec = await self.sandbox_spec_service.get_sandbox_spec(sandbox_spec_id)
        if sandbox_spec is None:
            return K8sAgentSandboxSpecInfo(id=sandbox_spec_id, command=None)
        return _as_k8s_spec(sandbox_spec)

    def _port_url(self, sandbox_name: str, port: int) -> str:
        """The router path for a port on a sandbox."""
        return (
            f'{self.router_url.rstrip("/")}/{self.k8s.namespace}/{sandbox_name}/{port}'
        )

    def _exposed_urls(
        self,
        sandbox_name: str,
        sandbox_spec: K8sAgentSandboxSpecInfo,
        session_api_key: str,
    ) -> list[ExposedUrl]:
        """The agent server, worker and VSCode URLs."""
        # The agent server gives VSCode the session API key as its connection
        # token on POST /api/init.
        vscode_url = (
            f'{self._port_url(sandbox_name, sandbox_spec.vscode_port)}'
            f'/?tkn={session_api_key}&folder={sandbox_spec.working_dir}'
        )
        return [
            ExposedUrl(
                name=AGENT_SERVER,
                url=self._port_url(sandbox_name, sandbox_spec.agent_server_port),
                port=sandbox_spec.agent_server_port,
            ),
            ExposedUrl(
                name=WORKER_1,
                url=self._port_url(sandbox_name, WORKER_1_PORT),
                port=WORKER_1_PORT,
            ),
            ExposedUrl(
                name=WORKER_2,
                url=self._port_url(sandbox_name, WORKER_2_PORT),
                port=WORKER_2_PORT,
            ),
            ExposedUrl(name=VSCODE, url=vscode_url, port=sandbox_spec.vscode_port),
        ]

    async def _to_sandbox_info(
        self,
        stored_sandbox: StoredSandbox,
        claim: dict | None,
    ) -> SandboxInfo:
        """Build a SandboxInfo from the stored row plus its claim.

        A row whose claim is gone is MISSING, which drives the
        archived-conversation UI.
        """
        status = _claim_status(claim)
        session_api_key = self._raw_key(stored_sandbox)
        exposed_urls = None
        sandbox_name = _sandbox_name(claim) if claim else None
        if (
            status == SandboxStatus.RUNNING
            and claim
            and session_api_key
            and sandbox_name
        ):
            sandbox_spec = await self._get_spec(stored_sandbox.sandbox_spec_id)
            exposed_urls = self._exposed_urls(
                sandbox_name, sandbox_spec, session_api_key
            )
        else:
            session_api_key = None

        return SandboxInfo(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
            sandbox_spec_id=stored_sandbox.sandbox_spec_id,
            status=status,
            session_api_key=session_api_key,
            exposed_urls=exposed_urls,
            created_at=stored_sandbox.created_at,
            status_detail=(
                _describe(_condition(claim))
                if claim and status == SandboxStatus.ERROR
                else None
            ),
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
        """Search for sandboxes: one query for the page, then each row's claim."""
        page = await search_stored_sandboxes(
            self.db_session,
            self.user_context,
            K8S_AGENT_SANDBOX_BACKEND,
            page_id,
            limit,
        )
        claims = await asyncio.gather(
            *(self.k8s.get_claim(stored_sandbox.id) for stored_sandbox in page.items)
        )
        sandboxes = [
            await self._to_sandbox_info(stored_sandbox, claim)
            for stored_sandbox, claim in zip(page.items, claims, strict=True)
        ]
        return SandboxPage(items=sandboxes, next_page_id=page.next_page_id)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        """Get a single sandbox."""
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return None
        return await self._to_sandbox_info(
            stored_sandbox, await self.k8s.get_claim(sandbox_id)
        )

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        """Get a single sandbox by session API key, on the hash index."""
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session,
            self.user_context,
            K8S_AGENT_SANDBOX_BACKEND,
            session_api_key,
        )
        if stored_sandbox is None:
            return None
        return await self._to_sandbox_info(
            stored_sandbox, await self.k8s.get_claim(stored_sandbox.id)
        )

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        """Get sandbox identity by session API key.

        An indexed lookup with no Kubernetes call. This runs on the webhook
        path, once per batch of agent events.
        """
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session,
            self.user_context,
            K8S_AGENT_SANDBOX_BACKEND,
            session_api_key,
        )
        if stored_sandbox is None:
            return None
        return SandboxRecord(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_sandbox(
        self, sandbox_spec_id: str | None = None, sandbox_id: str | None = None
    ) -> SandboxInfo:
        """Claim a warm sandbox and initialize its agent server.

        ``sandbox_id`` is ignored. The id is the claim's name, which the SDK
        picks.

        The ``/api/init`` handshake completes before this returns. A warm
        pod's ``/alive``, ``/health`` and ``/ready`` answer 200 while its agent
        server is dormant, so a caller waiting on readiness would otherwise be
        handed a server that rejects every ``/api`` call with a 503.
        """
        # Every sandbox has an owner. Check before pause_old_sandboxes, which
        # would reach every user's sandboxes as ADMIN.
        user_id = await require_user_id(self.user_context)
        if not self.router_url:
            raise SandboxError(MISSING_ROUTER_URL)

        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        user_default_spec_id = await self.user_context.get_default_sandbox_spec_id()
        sandbox_spec = _as_k8s_spec(
            await resolve_sandbox_spec(
                sandbox_spec_id,
                user_default_spec_id,
                self.sandbox_spec_service,
                _logger,
            )
        )
        # Checked before the claim: without the key the handshake cannot
        # succeed, so a warm pod would be spent on a 401.
        if _init_api_key(sandbox_spec) is None:
            raise SandboxError(
                f'Sandbox spec {sandbox_spec.id!r} {MISSING_INIT_API_KEY}'
            )

        claim_name, sandbox_name = await self.k8s.claim(
            sandbox_spec.id,
            labels={MANAGED_LABEL: 'true', OWNER_LABEL: owner_label_value(user_id)},
            timeout=self.claim_timeout_seconds,
        )

        session_api_key = base62.encodebytes(os.urandom(32))
        stored_sandbox = StoredSandbox(
            id=claim_name,
            backend=K8S_AGENT_SANDBOX_BACKEND,
            created_by_user_id=user_id,
            sandbox_spec_id=sandbox_spec.id,
            session_api_key_hash=hash_session_api_key(session_api_key),
            session_api_key=SecretStr(session_api_key),
            created_at=utc_now(),
        )
        try:
            self.db_session.add(stored_sandbox)
            await self.db_session.flush()
            await self._initialize_agent_server(
                sandbox_name, sandbox_spec, session_api_key
            )
        except BaseException:
            # Init is never retried: a pod that took a half-finished init is
            # not worth reasoning about when the pool has another one ready.
            await self._delete_claim_quietly(claim_name)
            raise

        return SandboxInfo(
            id=claim_name,
            created_by_user_id=user_id,
            sandbox_spec_id=sandbox_spec.id,
            status=SandboxStatus.RUNNING,
            session_api_key=session_api_key,
            exposed_urls=self._exposed_urls(
                sandbox_name, sandbox_spec, session_api_key
            ),
            created_at=stored_sandbox.created_at,
        )

    async def _delete_claim_quietly(self, claim_name: str) -> None:
        """Delete a claim on a failure path, without masking the original error."""
        try:
            await self.k8s.delete_claim(claim_name)
        except Exception:
            _logger.warning(
                f'Could not delete SandboxClaim {claim_name} after a failed start'
            )

    async def _initialize_agent_server(
        self,
        sandbox_name: str,
        sandbox_spec: K8sAgentSandboxSpecInfo,
        session_api_key: str,
        allow_initialized: bool = False,
    ) -> None:
        """Wait for the dormant agent server and hand it its runtime config.

        With ``allow_initialized``, an agent server that is already
        initialized is left alone.
        """
        agent_server_url = self._port_url(sandbox_name, sandbox_spec.agent_server_port)
        if not await self._wait_for_dormant(agent_server_url, allow_initialized):
            return

        init_api_key = _init_api_key(sandbox_spec)
        headers = {'X-Init-API-Key': init_api_key} if init_api_key else {}
        try:
            response = await self.httpx_client.post(
                f'{agent_server_url}/api/init',
                json=self._build_init_request(sandbox_spec, session_api_key),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise SandboxError(
                f'Could not reach the agent server in sandbox {sandbox_name}'
            ) from exc
        if response.status_code != 200:
            _logger.error(
                f'Agent server init for sandbox {sandbox_name} returned '
                f'{response.status_code}: {response.text}'
            )
            if response.status_code == 401:
                raise SandboxError(
                    f'The agent server in sandbox {sandbox_name} rejected the init '
                    'API key. It must match the OH_SECRET_KEY in the SandboxTemplate '
                    f'behind warm pool {sandbox_spec.id!r}.'
                )
            raise SandboxError(f'Failed to initialize sandbox {sandbox_name}')

    async def _wait_for_dormant(
        self, agent_server_url: str, allow_initialized: bool
    ) -> bool:
        """Poll ``GET /api/init`` until the agent server can be claimed.

        Returns True when it is dormant, and False when it is already
        initialized and ``allow_initialized`` is set. The router answers 502
        until the sandbox's DNS name resolves, which is expected and not fatal.
        """
        deadline = time.monotonic() + self.init_timeout_seconds
        while True:
            try:
                response = await self.httpx_client.get(f'{agent_server_url}/api/init')
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
        self, sandbox_spec: K8sAgentSandboxSpecInfo, session_api_key: str
    ) -> dict[str, Any]:
        """Build the ``POST /api/init`` body.

        The agent server forbids extra fields, so this carries only what it
        accepts.
        """
        # The spec's working dir is the project checkout; its parent is the
        # workspace root the agent server keeps its own state under, which is
        # the template's volume.
        working_dir = sandbox_spec.working_dir.rstrip('/')
        workspace_dir = dirname(working_dir)
        if workspace_dir in ('', '/'):
            workspace_dir = working_dir
        body: dict[str, Any] = {
            'session_api_keys': [session_api_key],
            # What the agent server uses when it has no secret key of its own.
            # Sent because the template boots it holding the shared init key.
            # It lives as long as the session key, so secrets persisted on the
            # volume still decrypt after a resume.
            'secret_key': session_api_key,
            'conversations_path': f'{workspace_dir}/conversations',
            'bash_events_dir': f'{workspace_dir}/bash_events',
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
                'Neither AGENT_SANDBOX_WEBHOOK_BASE_URL nor a non-localhost '
                'OH_WEB_URL is set, so sandboxes cannot post events back to the '
                'app server. Conversations will start but will not receive '
                'agent events.'
            )

        return body

    async def resume_sandbox(self, sandbox_id: str) -> bool:
        """Resume a paused sandbox.

        The Sandbox gets a new pod on the same volumes, and its agent server
        boots dormant again, so the handshake is repeated with the key already
        on the row. A sandbox that is not paused is left as it is.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        claim = await self.k8s.get_claim(sandbox_id)
        if claim is None:
            return False
        status = _claim_status(claim)
        if status == SandboxStatus.MISSING:
            return False
        if status != SandboxStatus.PAUSED:
            return True

        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        sandbox_name = _sandbox_name(claim)
        session_api_key = self._raw_key(stored_sandbox)
        if sandbox_name is None or session_api_key is None:
            return False
        if not await self.k8s.set_operating_mode(sandbox_name, 'Running'):
            return False
        # The claim reports the suspension only once the Sandbox is not ready,
        # so this wait cannot pass on the pod from before the pause.
        await self.k8s.wait_for_sandbox(sandbox_name, self.claim_timeout_seconds)
        await self._initialize_agent_server(
            sandbox_name,
            await self._get_spec(stored_sandbox.sandbox_spec_id),
            session_api_key,
            allow_initialized=True,
        )
        return True

    async def pause_sandbox(self, sandbox_id: str) -> bool:
        """Pause a running sandbox by suspending it.

        The pod is deleted. The Sandbox, its Service and its volumes stay, so
        the router path and the stored key are the same after a resume.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        claim = await self.k8s.get_claim(sandbox_id)
        if claim is None or _claim_status(claim) == SandboxStatus.MISSING:
            return False
        sandbox_name = _sandbox_name(claim)
        if sandbox_name is None:
            raise SandboxError(
                f'Sandbox {sandbox_id} has no pod yet, so there is nothing to pause'
            )
        return await self.k8s.set_operating_mode(sandbox_name, 'Suspended')

    async def delete_sandbox(self, sandbox_id: str) -> bool:
        """Delete a sandbox and its row.

        Deleting the claim deletes its Sandbox, pod and volumes. Returns False
        only when there is no such sandbox or the caller may not see it. A
        failed delete raises ``SandboxDeleteRetryError`` and keeps the row, so
        a live sandbox is never reported as gone.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        try:
            await self.k8s.delete_claim(sandbox_id)
        except SandboxError as exc:
            _logger.exception(f'Error deleting sandbox {sandbox_id}', stack_info=True)
            raise SandboxDeleteRetryError(
                f'Could not complete delete for sandbox {sandbox_id}: {exc}'
            ) from exc
        await self.db_session.delete(stored_sandbox)
        return True


class K8sAgentSandboxServiceInjector(SandboxServiceInjector):
    """Dependency injector for k8s agent-sandbox sandbox services."""

    namespace: str = Field(
        default_factory=lambda: os.getenv('AGENT_SANDBOX_NAMESPACE', 'default'),
        description=(
            'Namespace of the SandboxWarmPool the app claims from. Defaults to '
            'the AGENT_SANDBOX_NAMESPACE env var.'
        ),
    )
    router_url: str = Field(
        default_factory=lambda: os.getenv('AGENT_SANDBOX_ROUTER_URL', ''),
        description=(
            "Public URL of the agent-sandbox router's path-routing prefix, for "
            'example https://openhands.example.com/sandbox-router. Browsers and '
            'the app reach a sandbox at {router_url}/{namespace}/{sandbox}/{port}. '
            'Defaults to the AGENT_SANDBOX_ROUTER_URL env var.'
        ),
    )
    webhook_base_url: str | None = Field(
        default_factory=lambda: os.getenv('AGENT_SANDBOX_WEBHOOK_BASE_URL') or None,
        description=(
            'The app URL sandboxes post events to, when it differs from OH_WEB_URL, '
            'such as an in-cluster Service URL. Defaults to the '
            'AGENT_SANDBOX_WEBHOOK_BASE_URL env var.'
        ),
    )
    max_num_sandboxes: int = Field(
        default=10,
        description='Maximum number of sandboxes allowed to run simultaneously',
    )
    claim_timeout_seconds: int = Field(
        default=300,
        description=(
            'The max time to wait for a claim to get a ready sandbox, or for a '
            'resumed one to get its new pod. An empty pool means a cold start.'
        ),
    )
    init_timeout_seconds: int = Field(
        default=120,
        description=(
            'The max time to wait for a claimed sandbox to reach the dormant '
            'state before its start is considered failed.'
        ),
    )
    poll_interval: float = Field(
        default=1.0,
        description='Seconds between polls of a starting agent server',
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
        # One per request: the SDK client holds on to every sandbox it creates
        # until it is closed.
        k8s = AgentSandboxClient(self.namespace)
        try:
            async with (
                get_user_context(state, request) as user_context,
                get_httpx_client(state, request) as httpx_client,
                get_sandbox_spec_service(state, request) as sandbox_spec_service,
                get_db_session(state, request) as db_session,
            ):
                yield K8sAgentSandboxService(
                    sandbox_spec_service=sandbox_spec_service,
                    user_context=user_context,
                    httpx_client=httpx_client,
                    db_session=db_session,
                    k8s=k8s,
                    router_url=self.router_url,
                    max_num_sandboxes=self.max_num_sandboxes,
                    claim_timeout_seconds=self.claim_timeout_seconds,
                    init_timeout_seconds=self.init_timeout_seconds,
                    poll_interval=self.poll_interval,
                    web_url=config.web_url,
                    webhook_base_url=self.webhook_base_url,
                    permitted_cors_origins=config.permitted_cors_origins,
                )
        finally:
            await k8s.close()
