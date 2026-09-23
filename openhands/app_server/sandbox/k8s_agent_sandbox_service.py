import asyncio
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from posixpath import dirname
from typing import Any, AsyncGenerator, Callable
from urllib.parse import urlsplit

import base62
import httpx
import urllib3
from fastapi import Request
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
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
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext

_logger = logging.getLogger(__name__)

CLAIM_GROUP = 'extensions.agents.x-k8s.io'
SANDBOX_GROUP = 'agents.x-k8s.io'
API_VERSION = 'v1beta1'

# Ownership lives in the sandbox table (see `sandbox_store`). These tag the
# claims the app makes, so that one with no row can be found. The owner label
# is a hash because a label value cannot hold every user id.
MANAGED_LABEL = 'openhands.dev/managed'
OWNER_LABEL = 'openhands.dev/owner'
USER_ID_ANNOTATION = 'openhands.dev/user-id'
SANDBOX_SPEC_ID_ANNOTATION = 'openhands.dev/sandbox-spec-id'

WORKER_1_PORT = 8011
WORKER_2_PORT = 8012

# A claim mirrors its sandbox's Ready condition. This is its reason while the
# sandbox is suspended.
SUSPENDED_REASON = 'SandboxSuspended'
# Ready reasons that do not clear on their own. Any other not-ready reason is
# a claim still being served: adoption, a cold start, a booting pod.
FAILED_REASONS = frozenset(
    {
        'TemplateNotFound',
        'WarmPoolNotFound',
        'InvalidMetadata',
        'EnvVarsInjectionRejected',
        'VolumeClaimTemplatesError',
        'ClaimExpired',
        'SandboxExpired',
        'PodFailed',
        'PodSucceeded',
    }
)

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


# VSCode connection URLs are fetched from the agent server rather than built
# locally (see `_resolve_vscode_url`), so they are cached to keep `get_sandbox`
# off the network. Each entry holds the time its claim last became ready. A
# resume boots a new pod with a new VSCode token, which moves that time, so the
# entry is fetched again.
VSCODE_URL_CACHE_SIZE = 1024
_vscode_urls: dict[str, tuple[str, str]] = {}


def _cache_vscode_url(claim_name: str, ready_since: str, url: str) -> None:
    """Cache a VSCode URL, dropping the oldest entry when the cache is full.

    Claims deleted outside this service are never removed here, so the cache
    needs a bound of its own.
    """
    _vscode_urls.pop(claim_name, None)
    while len(_vscode_urls) >= VSCODE_URL_CACHE_SIZE:
        _vscode_urls.pop(next(iter(_vscode_urls)))
    _vscode_urls[claim_name] = (ready_since, url)


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
    """The agent-sandbox API calls this backend makes, in one namespace.

    The kubernetes client is synchronous, so each call runs in a worker thread.
    A 404 on a named object reads as "gone" and returns None. Every other
    failure raises ``SandboxError``.
    """

    def __init__(self, api_client: k8s_client.ApiClient, namespace: str):
        self._api = k8s_client.CustomObjectsApi(api_client)
        self.namespace = namespace

    async def _call(
        self,
        action: str,
        method: Callable[..., Any],
        *args: Any,
        missing_ok: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except ApiException as exc:
            if missing_ok and exc.status == 404:
                return None
            if exc.status in (401, 403):
                raise SandboxError(
                    f'Kubernetes refused to {action} in namespace '
                    f'{self.namespace!r} ({exc.status} {exc.reason}). The app '
                    'needs create, get, list and delete on sandboxclaims, and '
                    'get and patch on sandboxes, in that namespace.'
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
        except urllib3.exceptions.HTTPError as exc:
            raise SandboxError(
                f'Could not {action}: the Kubernetes API is unreachable ({exc})'
            ) from exc

    async def create_claim(self, body: dict) -> dict:
        return await self._call(
            'create a SandboxClaim',
            self._api.create_namespaced_custom_object,
            CLAIM_GROUP,
            API_VERSION,
            self.namespace,
            'sandboxclaims',
            body,
        )

    async def get_claim(self, name: str) -> dict | None:
        return await self._call(
            'read a SandboxClaim',
            self._api.get_namespaced_custom_object,
            CLAIM_GROUP,
            API_VERSION,
            self.namespace,
            'sandboxclaims',
            name,
            missing_ok=True,
        )

    async def list_claims(self, label_selector: str) -> list[dict]:
        result = await self._call(
            'list SandboxClaims',
            self._api.list_namespaced_custom_object,
            CLAIM_GROUP,
            API_VERSION,
            self.namespace,
            'sandboxclaims',
            label_selector=label_selector,
        )
        return result.get('items') or []

    async def delete_claim(self, name: str) -> bool:
        """Delete a claim, and with it its Sandbox, pod and volumes.

        Returns False when the claim was already gone.
        """
        result = await self._call(
            'delete a SandboxClaim',
            self._api.delete_namespaced_custom_object,
            CLAIM_GROUP,
            API_VERSION,
            self.namespace,
            'sandboxclaims',
            name,
            missing_ok=True,
        )
        return result is not None

    async def get_sandbox(self, name: str) -> dict | None:
        return await self._call(
            'read a Sandbox',
            self._api.get_namespaced_custom_object,
            SANDBOX_GROUP,
            API_VERSION,
            self.namespace,
            'sandboxes',
            name,
            missing_ok=True,
        )

    async def set_operating_mode(self, name: str, mode: str) -> dict | None:
        """Set a Sandbox's operatingMode (Running or Suspended)."""
        return await self._call(
            'update a Sandbox',
            self._api.patch_namespaced_custom_object,
            SANDBOX_GROUP,
            API_VERSION,
            self.namespace,
            'sandboxes',
            name,
            {'spec': {'operatingMode': mode}},
            missing_ok=True,
        )


def _load_api_client(kube_context: str | None) -> k8s_client.ApiClient:
    """The pod's ServiceAccount when running in the cluster, else a kubeconfig.

    The kubeconfig is ``KUBECONFIG`` or ``~/.kube/config``. Naming a context
    skips the in-cluster credentials.
    """
    configuration = k8s_client.Configuration()
    if kube_context is None:
        try:
            k8s_config.load_incluster_config(client_configuration=configuration)
            return k8s_client.ApiClient(configuration)
        except ConfigException:
            pass
    try:
        k8s_config.load_kube_config(
            context=kube_context,
            client_configuration=configuration,
            persist_config=False,
        )
    except ConfigException as exc:
        raise SandboxError(
            'No Kubernetes credentials: the app is not running in a pod and no '
            f'kubeconfig could be loaded ({exc})'
        ) from exc
    return k8s_client.ApiClient(configuration)


_clients: dict[tuple[str, str | None], AgentSandboxClient] = {}


def get_agent_sandbox_client(
    namespace: str, kube_context: str | None
) -> AgentSandboxClient:
    """One client per namespace and context, for the life of the process."""
    key = (namespace, kube_context)
    if key not in _clients:
        _clients[key] = AgentSandboxClient(_load_api_client(kube_context), namespace)
    return _clients[key]


@dataclass
class K8sAgentSandboxService(SandboxService):
    """Sandbox service backed by kubernetes-sigs/agent-sandbox warm pools.

    The operator creates a SandboxTemplate and a SandboxWarmPool ahead of time.
    The pool keeps agent server pods booted in deferred-init mode, and
    ``start_sandbox`` claims one with a SandboxClaim, then completes the
    ``/api/init`` handshake before returning. The pod booted before any user
    existed, so everything per user reaches it through that handshake.

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

    async def _live_claims(self, wanted_ids: set[str]) -> dict[str, dict]:
        """The claims behind a page of rows, indexed by name.

        The rows have already decided ownership. The owner label only narrows
        the list. ``ADMIN`` reads across users, so it lists without it.
        """
        if not wanted_ids:
            return {}
        selector = f'{MANAGED_LABEL}=true'
        if self.user_context != ADMIN:
            user_id = await require_user_id(self.user_context)
            selector += f',{OWNER_LABEL}={owner_label_value(user_id)}'
        claims = await self.k8s.list_claims(selector)
        return {
            claim['metadata']['name']: claim
            for claim in claims
            if claim['metadata']['name'] in wanted_ids
        }

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

    @property
    def _router_origin(self) -> str:
        """The router URL's scheme and host, without its path."""
        parts = urlsplit(self.router_url)
        return f'{parts.scheme}://{parts.netloc}'

    async def _resolve_vscode_url(
        self,
        claim: dict,
        sandbox_name: str,
        sandbox_spec: K8sAgentSandboxSpecInfo,
        session_api_key: str,
    ) -> str | None:
        """The VSCode connection URL, from the cache or the agent server.

        The URL cannot be built locally: under deferred init, VSCode takes its
        connection token at boot, so the token is unrelated to the session API
        key. The agent server puts VSCode's base path, the router path from
        ``OH_VSCODE_BASE_PATH``, after ``base_url``, so ``base_url`` is the
        router's origin.

        Returns None when the agent server cannot be reached, and when the URL
        is not under the sandbox's router path: a template without the base
        path, whose link would open the app instead of VSCode.
        """
        claim_name = claim['metadata']['name']
        ready_since = _condition(claim).get('lastTransitionTime', '')
        cached = _vscode_urls.get(claim_name)
        if cached and cached[0] == ready_since:
            return cached[1]
        agent_server_url = self._port_url(sandbox_name, sandbox_spec.agent_server_port)
        try:
            response = await self.httpx_client.get(
                f'{agent_server_url}/api/vscode/url',
                params={
                    'base_url': self._router_origin,
                    'workspace_dir': sandbox_spec.working_dir,
                },
                headers={'X-Session-API-Key': session_api_key},
            )
            response.raise_for_status()
            url = response.json().get('url')
        except Exception as exc:
            _logger.info(f'No VSCode URL for sandbox {claim_name}: {exc}')
            return None
        if not url:
            return None
        vscode_path = self._port_url(sandbox_name, sandbox_spec.vscode_port) + '/'
        if not url.startswith(vscode_path):
            _logger.warning(
                f'The VSCode URL for sandbox {claim_name} is not under '
                f"{vscode_path}. Set OH_VSCODE_BASE_PATH in the pool's "
                'SandboxTemplate to <router prefix>/$(POD_NAMESPACE)/$(POD_NAME)/'
                f'{sandbox_spec.vscode_port}.'
            )
            return None
        _cache_vscode_url(claim_name, ready_since, url)
        return url

    def _exposed_urls(
        self,
        sandbox_name: str,
        sandbox_spec: K8sAgentSandboxSpecInfo,
        vscode_url: str | None = None,
    ) -> list[ExposedUrl]:
        """The agent server, worker and VSCode URLs."""
        exposed_urls = [
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
        ]
        if vscode_url:
            exposed_urls.append(
                ExposedUrl(name=VSCODE, url=vscode_url, port=sandbox_spec.vscode_port)
            )
        return exposed_urls

    async def _to_sandbox_info(
        self,
        stored_sandbox: StoredSandbox,
        claim: dict | None,
        with_vscode_url: bool = True,
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
            vscode_url = None
            if with_vscode_url:
                vscode_url = await self._resolve_vscode_url(
                    claim, sandbox_name, sandbox_spec, session_api_key
                )
            exposed_urls = self._exposed_urls(sandbox_name, sandbox_spec, vscode_url)
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
        """Search for sandboxes: one query for the page, one list call for its claims.

        VSCode URLs are left out. Resolving one costs an HTTP call to the
        sandbox, and search runs on every conversation start through
        ``pause_old_sandboxes``. The frontend reads that URL through
        ``batch_get_sandboxes``, which still resolves it.
        """
        page = await search_stored_sandboxes(
            self.db_session,
            self.user_context,
            K8S_AGENT_SANDBOX_BACKEND,
            page_id,
            limit,
        )
        claims = await self._live_claims({row.id for row in page.items})
        sandboxes = [
            await self._to_sandbox_info(
                stored_sandbox, claims.get(stored_sandbox.id), with_vscode_url=False
            )
            for stored_sandbox in page.items
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

    def _claim_body(self, claim_name: str, warm_pool: str, user_id: str) -> dict:
        """A claim on the pool. It sets no env and no volumes: either one
        makes the controller cold-start a new pod instead of adopting a warm one.
        """
        return {
            'apiVersion': f'{CLAIM_GROUP}/{API_VERSION}',
            'kind': 'SandboxClaim',
            'metadata': {
                'name': claim_name,
                'namespace': self.k8s.namespace,
                'labels': {
                    MANAGED_LABEL: 'true',
                    OWNER_LABEL: owner_label_value(user_id),
                },
                'annotations': {
                    USER_ID_ANNOTATION: user_id,
                    SANDBOX_SPEC_ID_ANNOTATION: warm_pool,
                },
            },
            'spec': {'warmPoolRef': {'name': warm_pool}},
        }

    async def start_sandbox(
        self, sandbox_spec_id: str | None = None, sandbox_id: str | None = None
    ) -> SandboxInfo:
        """Claim a warm sandbox and initialize its agent server.

        ``sandbox_id`` is ignored. The id is the claim's name, which has to be
        a DNS label, so the service picks it.

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

        claim_name = f'oh-{uuid.uuid4().hex}'
        await self.k8s.create_claim(
            self._claim_body(claim_name, sandbox_spec.id, user_id)
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
            sandbox_name, claim = await self._wait_for_claim(claim_name)
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
                sandbox_name,
                sandbox_spec,
                await self._resolve_vscode_url(
                    claim, sandbox_name, sandbox_spec, session_api_key
                ),
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

    async def _wait_for_claim(self, claim_name: str) -> tuple[str, dict]:
        """Poll a new claim until it holds a ready sandbox.

        Returns the sandbox's name and the claim.

        Adopting a warm sandbox is near instant. With the pool empty, the
        controller cold-starts one from the template, which can take minutes
        when the node has to pull the image.
        """
        deadline = time.monotonic() + self.claim_timeout_seconds
        while True:
            claim = await self.k8s.get_claim(claim_name)
            if claim is None:
                raise SandboxError(
                    f'SandboxClaim {claim_name} was deleted while starting'
                )
            ready = _condition(claim)
            sandbox_name = _sandbox_name(claim)
            if ready.get('status') == 'True' and sandbox_name:
                return sandbox_name, claim
            if ready.get('reason') == 'WarmPoolNotFound':
                raise SandboxError(
                    f'SandboxWarmPool {claim["spec"]["warmPoolRef"]["name"]!r} does '
                    f'not exist in namespace {self.k8s.namespace!r}. The operator '
                    'creates it ahead of time, and AGENT_SANDBOX_WARM_POOL names it.'
                )
            if ready.get('reason') in FAILED_REASONS:
                raise SandboxError(
                    f'SandboxClaim {claim_name} failed: {_describe(ready)}'
                )
            if time.monotonic() >= deadline:
                last = f' Last state: {_describe(ready)}.' if ready else ''
                raise SandboxError(
                    f'SandboxClaim {claim_name} did not get a ready sandbox within '
                    f'{self.claim_timeout_seconds}s.{last}'
                )
            await asyncio.sleep(self.poll_interval)

    async def _wait_for_sandbox_ready(self, sandbox_name: str, generation: int) -> None:
        """Poll a resumed Sandbox until its new pod is ready.

        The Ready condition must have seen the resume (``observedGeneration``),
        or a pod still shutting down from the pause would pass.
        """
        deadline = time.monotonic() + self.claim_timeout_seconds
        while True:
            sandbox = await self.k8s.get_sandbox(sandbox_name)
            if sandbox is None:
                raise SandboxError(f'Sandbox {sandbox_name} was deleted while resuming')
            ready = _condition(sandbox)
            if (
                ready.get('status') == 'True'
                and ready.get('observedGeneration', 0) >= generation
            ):
                return
            if time.monotonic() >= deadline:
                raise SandboxError(
                    f'Sandbox {sandbox_name} was not ready within '
                    f'{self.claim_timeout_seconds}s of resuming. Last state: '
                    f'{_describe(ready)}.'
                )
            await asyncio.sleep(self.poll_interval)

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
        sandbox = await self.k8s.set_operating_mode(sandbox_name, 'Running')
        if sandbox is None:
            return False
        await self._wait_for_sandbox_ready(
            sandbox_name, sandbox['metadata']['generation']
        )
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
        return await self.k8s.set_operating_mode(sandbox_name, 'Suspended') is not None

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
            if not await self.k8s.delete_claim(sandbox_id):
                _logger.info(f'SandboxClaim {sandbox_id} already gone; removing row')
        except SandboxError as exc:
            _logger.exception(f'Error deleting sandbox {sandbox_id}', stack_info=True)
            raise SandboxDeleteRetryError(
                f'Could not complete delete for sandbox {sandbox_id}: {exc}'
            ) from exc
        await self.db_session.delete(stored_sandbox)
        _vscode_urls.pop(sandbox_id, None)
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
    kube_context: str | None = Field(
        default_factory=lambda: os.getenv('AGENT_SANDBOX_KUBE_CONTEXT') or None,
        description=(
            'Kubeconfig context to use. Unset, the app uses its pod ServiceAccount '
            'when running in the cluster, and the current kubeconfig context '
            'otherwise. Defaults to the AGENT_SANDBOX_KUBE_CONTEXT env var.'
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
        description='Seconds between polls of a claim, a sandbox or an agent server',
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
        k8s = get_agent_sandbox_client(self.namespace, self.kube_context)
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
