import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from posixpath import dirname
from typing import Any, AsyncGenerator

import base62
import httpx
from e2b import (
    AsyncSandbox,
    AuthenticationException,
    SandboxException,
    SandboxNotFoundException,
    SandboxQuery,
    SandboxState,
)
from e2b import SandboxInfo as E2BSandboxInfo
from fastapi import Request
from pydantic import Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.utils import utc_now
from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
from openhands.app_server.sandbox.e2b_sandbox_spec_service import (
    E2BSandboxSpecInfo,
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
    E2B_BACKEND,
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

# Ownership lives in the sandbox table (see `sandbox_store`). These metadata
# keys tag managed sandboxes so that one with no row can be found.
MANAGED_METADATA_KEY = 'oh_managed'
SANDBOX_SPEC_ID_METADATA_KEY = 'oh_spec_id'
CREATED_BY_USER_ID_METADATA_KEY = 'oh_user_id'

WORKER_1_PORT = 8011
WORKER_2_PORT = 8012

STATUS_MAPPING = {
    SandboxState.RUNNING: SandboxStatus.RUNNING,
    SandboxState.PAUSED: SandboxStatus.PAUSED,
}

# VSCode connection URLs are fetched from the agent server rather than built
# locally (see `_resolve_vscode_url`), so they are cached for the life of the
# process to keep `get_sandbox` off the network. A miss - after an app server
# restart, or an eviction - costs one lazy re-fetch.
VSCODE_URL_CACHE_SIZE = 1024
_vscode_urls: dict[str, str] = {}


def _cache_vscode_url(e2b_sandbox_id: str, url: str) -> None:
    """Cache a VSCode URL, dropping the oldest entry when the cache is full.

    Sandboxes reaped by E2B are never deleted through this service, so the
    cache needs a bound of its own.
    """
    while len(_vscode_urls) >= VSCODE_URL_CACHE_SIZE:
        _vscode_urls.pop(next(iter(_vscode_urls)))
    _vscode_urls[e2b_sandbox_id] = url


def _as_e2b_spec(sandbox_spec: SandboxSpecInfo) -> E2BSandboxSpecInfo:
    """Narrow a spec to the E2B shape, filling defaults for a plain spec."""
    if isinstance(sandbox_spec, E2BSandboxSpecInfo):
        return sandbox_spec
    return E2BSandboxSpecInfo(**sandbox_spec.model_dump())


def _init_api_key(sandbox_spec: E2BSandboxSpecInfo) -> str | None:
    """The template's init key, treating an empty value as unset."""
    if sandbox_spec.init_api_key is None:
        return None
    return sandbox_spec.init_api_key.get_secret_value() or None


# `AuthenticationException` is the one E2B error that does not inherit from
# `SandboxException`, so it slips past every provider error handler unless it
# is caught by name. It has to be caught at each call site, and reported as
# itself: a rejected key must not be mistaken for a missing sandbox.
E2B_AUTH_FAILURE = (
    'E2B rejected the credentials. Check E2B_API_KEY, and E2B_API_URL on a '
    'self hosted cluster - a key is only valid against the control plane that '
    'issued it.'
)


def _auth_error(exc: AuthenticationException) -> SandboxError:
    return SandboxError(f'{E2B_AUTH_FAILURE} ({exc})')


# E2B rejects a create it has no room for. The failure is permanent - it is
# the template's footprint against the nodes' capacity, not a busy moment - so
# it is reported rather than retried.
PLACEMENT_FAILURE_MARKER = 'failed to place sandbox'

MISSING_INIT_API_KEY = (
    'has no init API key. The E2B template boots its agent server with a '
    'static OH_SECRET_KEY, and the app server must be configured with the '
    'same value: set E2B_INIT_API_KEY (or '
    'OH_SANDBOX_SPEC_SPECS_0_INIT_API_KEY) to the key the template was built '
    'with - scripts/e2b/build_template.py prints it.'
)


@dataclass
class E2BSandboxService(SandboxService):
    """Sandbox service backed by E2B Firecracker microVMs.

    Sandboxes are created from an E2B template that boots the agent server in
    deferred-init mode, and ``start_sandbox`` completes the ``/api/init``
    handshake before returning. Ownership, spec identity and the session API
    key live in the sandbox table (see ``sandbox_store``), the key encrypted
    at rest because E2B offers nowhere to read it back from. The matching E2B
    metadata is written too, as the tag a reconciler needs.

    E2B requires a publicly reachable ``OH_WEB_URL`` for agent server event
    callbacks. There is no polling fallback for this backend, so conversations
    started against a localhost app server will not receive events.
    """

    sandbox_spec_service: SandboxSpecService
    user_context: UserContext
    httpx_client: httpx.AsyncClient
    db_session: AsyncSession
    api_key: str
    domain: str
    timeout_seconds: int
    max_num_sandboxes: int
    init_timeout_seconds: int
    init_poll_interval: float
    resume_retries: int
    resume_retry_interval: float
    api_url: str | None = None
    web_url: str | None = None
    permitted_cors_origins: list[str] = field(default_factory=list)

    @property
    def _api_params(self) -> dict[str, Any]:
        """Connection options passed to every E2B SDK call.

        Passed explicitly so the backend is driven by its injector config
        rather than by whatever E2B_* variables happen to be in the process
        environment.
        """
        params: dict[str, Any] = {'api_key': self.api_key, 'domain': self.domain}
        if self.api_url:
            params['api_url'] = self.api_url
        return params

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    async def _get_stored_sandbox(self, sandbox_id: str) -> StoredSandbox | None:
        """Get a sandbox row, or None when the caller may not see it."""
        return await get_stored_sandbox(
            self.db_session, self.user_context, E2B_BACKEND, sandbox_id
        )

    async def _get_info(self, e2b_sandbox_id: str) -> E2BSandboxInfo | None:
        """Get E2B's info for a sandbox, or None when E2B has no such one."""
        try:
            return await AsyncSandbox.get_info(e2b_sandbox_id, **self._api_params)
        except AuthenticationException as exc:
            raise _auth_error(exc) from exc
        except SandboxNotFoundException:
            return None
        except SandboxException as exc:
            # A malformed id is rejected by the API with 400 Invalid sandbox ID.
            _logger.debug(f'Sandbox lookup failed for {e2b_sandbox_id}: {exc}')
            return None

    async def _live_infos(self, wanted_ids: set[str]) -> dict[str, E2BSandboxInfo]:
        """Live E2B state for the sandboxes on a page, indexed by id.

        The rows have already decided ownership. The owner filter only narrows
        the list, so a row E2B no longer has costs a scan of the caller's
        sandboxes rather than of every sandbox in the team. ``ADMIN`` reads
        across users, so it lists without the filter. The loop stops once
        every wanted id is found.
        """
        metadata = {MANAGED_METADATA_KEY: 'true'}
        if self.user_context != ADMIN:
            metadata[CREATED_BY_USER_ID_METADATA_KEY] = await require_user_id(
                self.user_context
            )
        paginator = AsyncSandbox.list(
            query=SandboxQuery(
                metadata=metadata,
                state=[SandboxState.RUNNING, SandboxState.PAUSED],
            ),
            **self._api_params,
        )
        found: dict[str, E2BSandboxInfo] = {}
        while wanted_ids - found.keys() and paginator.has_next:
            try:
                items = await paginator.next_items()
            except AuthenticationException as exc:
                raise _auth_error(exc) from exc
            except SandboxException as exc:
                raise SandboxError(f'Could not list sandboxes: {exc}') from exc
            for item in items:
                if item.sandbox_id in wanted_ids:
                    found[item.sandbox_id] = item
        return found

    # ------------------------------------------------------------------
    # Info mapping
    # ------------------------------------------------------------------

    async def _get_spec(self, sandbox_spec_id: str) -> E2BSandboxSpecInfo:
        """Get the spec a sandbox was created from, or the default shape.

        A sandbox outlives an edit to the configured spec list, so a spec that
        is no longer offered still needs ports and a working dir to build URLs.
        """
        sandbox_spec = await self.sandbox_spec_service.get_sandbox_spec(sandbox_spec_id)
        if sandbox_spec is None:
            return E2BSandboxSpecInfo(id=sandbox_spec_id, command=None)
        return _as_e2b_spec(sandbox_spec)

    def _host_url(self, e2b_sandbox_id: str, port: int) -> str:
        """URL of a port exposed by a sandbox.

        Built locally rather than through ``get_host`` because that needs a live
        sandbox handle, and because ``list()`` results carry no sandbox domain.
        """
        return f'https://{port}-{e2b_sandbox_id}.{self.domain}'

    async def _resolve_vscode_url(
        self,
        e2b_sandbox_id: str,
        sandbox_spec: E2BSandboxSpecInfo,
        session_api_key: str,
    ) -> str | None:
        """The VSCode connection URL, from the cache or the agent server.

        The URL cannot be built locally: under deferred init the VSCode service
        captures its connection token at boot, while ``session_api_keys`` is
        still empty, so the token is unrelated to the session API key. Returns
        None when the agent server cannot be reached - a sandbox without a
        VSCode URL is still perfectly usable.
        """
        cached = _vscode_urls.get(e2b_sandbox_id)
        if cached:
            return cached
        agent_server_url = self._host_url(
            e2b_sandbox_id, sandbox_spec.agent_server_port
        )
        try:
            response = await self.httpx_client.get(
                f'{agent_server_url}/api/vscode/url',
                params={
                    'base_url': self._host_url(
                        e2b_sandbox_id, sandbox_spec.vscode_port
                    ),
                    'workspace_dir': sandbox_spec.working_dir,
                },
                headers={'X-Session-API-Key': session_api_key},
            )
            response.raise_for_status()
            url = response.json().get('url')
        except Exception as exc:
            _logger.info(f'No VSCode URL for sandbox {e2b_sandbox_id}: {exc}')
            return None
        if url:
            _cache_vscode_url(e2b_sandbox_id, url)
        return url

    async def _exposed_urls(
        self,
        e2b_sandbox_id: str,
        sandbox_spec: E2BSandboxSpecInfo,
        session_api_key: str,
        with_vscode_url: bool = True,
    ) -> list[ExposedUrl]:
        exposed_urls = [
            ExposedUrl(
                name=AGENT_SERVER,
                url=self._host_url(e2b_sandbox_id, sandbox_spec.agent_server_port),
                port=sandbox_spec.agent_server_port,
            ),
            ExposedUrl(
                name=WORKER_1,
                url=self._host_url(e2b_sandbox_id, WORKER_1_PORT),
                port=WORKER_1_PORT,
            ),
            ExposedUrl(
                name=WORKER_2,
                url=self._host_url(e2b_sandbox_id, WORKER_2_PORT),
                port=WORKER_2_PORT,
            ),
        ]
        if not with_vscode_url:
            return exposed_urls
        vscode_url = await self._resolve_vscode_url(
            e2b_sandbox_id, sandbox_spec, session_api_key
        )
        if vscode_url:
            exposed_urls.append(
                ExposedUrl(name=VSCODE, url=vscode_url, port=sandbox_spec.vscode_port)
            )
        return exposed_urls

    async def _to_sandbox_info(
        self,
        stored_sandbox: StoredSandbox,
        info: E2BSandboxInfo | None,
        session_api_key: str | None = None,
        with_vscode_url: bool = True,
    ) -> SandboxInfo:
        """Build a SandboxInfo from the stored row plus its live E2B state.

        A row E2B no longer knows about is MISSING, which drives the
        archived-conversation UI.
        """
        status = (
            SandboxStatus.MISSING
            if info is None
            else STATUS_MAPPING.get(info.state, SandboxStatus.ERROR)
        )

        exposed_urls = None
        if status == SandboxStatus.RUNNING and session_api_key:
            sandbox_spec = await self._get_spec(stored_sandbox.sandbox_spec_id)
            exposed_urls = await self._exposed_urls(
                stored_sandbox.id, sandbox_spec, session_api_key, with_vscode_url
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
        """Search for sandboxes.

        One query against the sandbox table for the page, then one E2B list call
        for the live state of everything on it. Paused sandboxes are asked for
        explicitly: E2B's default list shows running ones only, and a paused
        sandbox is a conversation the user can still resume.

        VSCode URLs are left out. Resolving one costs an HTTP call to the
        sandbox itself, and the only caller that needs it - the frontend - goes
        through ``batch_get_sandboxes``. ``pause_old_sandboxes`` and the
        conversation-start lookup read nothing but id, status and created_at,
        and they run on every conversation start.
        """
        page = await search_stored_sandboxes(
            self.db_session, self.user_context, E2B_BACKEND, page_id, limit
        )
        infos = await self._live_infos({row.id for row in page.items})
        sandboxes = await asyncio.gather(
            *[
                self._to_sandbox_info(
                    stored_sandbox,
                    infos.get(stored_sandbox.id),
                    self._raw_key(stored_sandbox),
                    with_vscode_url=False,
                )
                for stored_sandbox in page.items
            ]
        )
        return SandboxPage(items=list(sandboxes), next_page_id=page.next_page_id)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        """Get a single sandbox."""
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return None
        return await self._to_sandbox_info(
            stored_sandbox,
            await self._get_info(sandbox_id),
            self._raw_key(stored_sandbox),
        )

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        """Get a single sandbox by session API key, on the hash index."""
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, E2B_BACKEND, session_api_key
        )
        if stored_sandbox is None:
            return None
        return await self._to_sandbox_info(
            stored_sandbox,
            await self._get_info(stored_sandbox.id),
            self._raw_key(stored_sandbox),
        )

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        """Get sandbox identity by session API key.

        An indexed lookup with no E2B call. This runs on the webhook path, once
        per batch of agent events.
        """
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, E2B_BACKEND, session_api_key
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
        """Start a new sandbox and initialize its agent server.

        ``sandbox_id`` is ignored: E2B assigns the id and ``SandboxInfo.id`` is
        that id. Callers read the id back off the returned info, so the hint has
        nowhere to go.

        The ``/api/init`` handshake completes before this returns. It has to:
        ``/alive``, ``/health`` and ``/ready`` are root routes outside the
        deferred-init gate and answer 200 while the server is still dormant, so
        a caller waiting on readiness would otherwise be handed a server that
        rejects every ``/api`` call with a 503.
        """
        # Every sandbox has an owner. Check before pause_old_sandboxes, which
        # would reach every user's sandboxes as ADMIN.
        user_id = await require_user_id(self.user_context)

        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        user_default_spec_id = await self.user_context.get_default_sandbox_spec_id()
        sandbox_spec = _as_e2b_spec(
            await resolve_sandbox_spec(
                sandbox_spec_id,
                user_default_spec_id,
                self.sandbox_spec_service,
                _logger,
            )
        )

        # Checked before create: without the key the init handshake cannot
        # succeed, so the sandbox would be built only to be killed a moment
        # later with a 401 that says nothing about the actual cause.
        if _init_api_key(sandbox_spec) is None:
            raise SandboxError(
                f'Sandbox spec {sandbox_spec.id!r} {MISSING_INIT_API_KEY}'
            )

        metadata = {
            MANAGED_METADATA_KEY: 'true',
            SANDBOX_SPEC_ID_METADATA_KEY: sandbox_spec.id,
            CREATED_BY_USER_ID_METADATA_KEY: user_id,
        }

        try:
            sandbox = await AsyncSandbox.create(
                template=sandbox_spec.id,
                timeout=self.timeout_seconds,
                metadata=metadata,
                # The E2B default on timeout is to kill the sandbox. Pausing
                # parks an idle conversation as a snapshot instead, and
                # auto_resume wakes it on the next inbound request.
                lifecycle={'on_timeout': 'pause', 'auto_resume': True},
                **self._api_params,
            )
        except AuthenticationException as exc:
            raise _auth_error(exc) from exc
        except SandboxException as exc:
            _logger.exception('Failed to create sandbox', stack_info=True)
            if PLACEMENT_FAILURE_MARKER in str(exc).lower():
                raise SandboxError(
                    f'The E2B cluster could not place a sandbox for template '
                    f'{sandbox_spec.id!r}: no node had room for the CPU and '
                    'memory the template was built with. Compare the '
                    "template's size against the capacity of the cluster "
                    'nodes, and rebuild it smaller if needed '
                    '(scripts/e2b/build_template.py --cpu-count / --memory-mb).'
                ) from exc
            raise SandboxError('Failed to start sandbox') from exc

        e2b_sandbox_id = sandbox.sandbox_id
        session_api_key = base62.encodebytes(os.urandom(32))
        stored_sandbox = StoredSandbox(
            id=e2b_sandbox_id,
            backend=E2B_BACKEND,
            created_by_user_id=user_id,
            sandbox_spec_id=sandbox_spec.id,
            session_api_key_hash=hash_session_api_key(session_api_key),
            session_api_key=SecretStr(session_api_key),
            created_at=utc_now(),
        )
        try:
            # E2B assigns the id, so the row can only be written after create.
            # A sandbox whose row fails to write is killed with the rest.
            self.db_session.add(stored_sandbox)
            await self.db_session.flush()
            await self._initialize_agent_server(
                e2b_sandbox_id, sandbox_spec, session_api_key
            )
        except BaseException:
            # Init is never retried: the rotated secret_key means a second
            # attempt answers 401, which is indistinguishable from a wrong key.
            # Creating a sandbox is sub-second, so throwing away a failed claim
            # is cheaper than reasoning about a half-initialized one.
            await self._kill_quietly(e2b_sandbox_id)
            raise

        exposed_urls = await self._exposed_urls(
            e2b_sandbox_id, sandbox_spec, session_api_key
        )
        return SandboxInfo(
            id=e2b_sandbox_id,
            created_by_user_id=user_id,
            sandbox_spec_id=sandbox_spec.id,
            status=SandboxStatus.RUNNING,
            session_api_key=session_api_key,
            exposed_urls=exposed_urls,
            created_at=stored_sandbox.created_at,
        )

    async def _kill_quietly(self, e2b_sandbox_id: str) -> None:
        """Kill a sandbox on a failure path, without masking the original error."""
        try:
            await AsyncSandbox.kill(e2b_sandbox_id, **self._api_params)
        except Exception:
            _logger.warning(
                f'Could not kill sandbox {e2b_sandbox_id} after a failed init'
            )

    async def _initialize_agent_server(
        self,
        e2b_sandbox_id: str,
        sandbox_spec: E2BSandboxSpecInfo,
        session_api_key: str,
    ) -> None:
        """Wait for the dormant agent server and hand it its runtime config."""
        agent_server_url = self._host_url(
            e2b_sandbox_id, sandbox_spec.agent_server_port
        )
        await self._wait_for_dormant(agent_server_url)

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
                f'Could not reach the agent server for sandbox {e2b_sandbox_id}'
            ) from exc
        if response.status_code != 200:
            _logger.error(
                f'Agent server init for sandbox {e2b_sandbox_id} returned '
                f'{response.status_code}: {response.text}'
            )
            if response.status_code == 401:
                raise SandboxError(
                    f'The agent server in sandbox {e2b_sandbox_id} rejected the '
                    'init API key. It must match the OH_SECRET_KEY baked into '
                    f'template {sandbox_spec.id!r}.'
                )
            raise SandboxError(f'Failed to initialize sandbox {e2b_sandbox_id}')

    async def _wait_for_dormant(self, agent_server_url: str) -> None:
        """Poll ``GET /api/init`` until the agent server is ready to be claimed.

        ``/ready`` is not a discriminator here - it answers 200 while the server
        is dormant - so the init state is the only reliable signal. While
        uvicorn is still binding the port the E2B edge answers 502 rather than
        the agent server, which is expected and not fatal.
        """
        deadline = time.monotonic() + self.init_timeout_seconds
        while True:
            try:
                response = await self.httpx_client.get(f'{agent_server_url}/api/init')
                if response.status_code == 200:
                    state = response.json().get('state')
                    if state == 'dormant':
                        return
                    if state == 'ready':
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
            await asyncio.sleep(self.init_poll_interval)

    def _build_init_request(
        self, sandbox_spec: E2BSandboxSpecInfo, session_api_key: str
    ) -> dict[str, Any]:
        """Build the ``POST /api/init`` body.

        The agent server forbids extra fields, so this carries only what it
        accepts. ``secret_key`` is rotated per sandbox: it encrypts secrets at
        rest inside the sandbox, and the template ships with a static value
        shared by every sandbox built from it.
        """
        # The spec's working dir is the project checkout; its parent is the
        # workspace root the agent server keeps its own state under. None of
        # these directories need to exist beforehand.
        working_dir = sandbox_spec.working_dir.rstrip('/')
        workspace_dir = dirname(working_dir)
        if workspace_dir in ('', '/'):
            workspace_dir = working_dir
        body: dict[str, Any] = {
            'session_api_keys': [session_api_key],
            'secret_key': base62.encodebytes(os.urandom(32)),
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

        if self.web_url and 'localhost' not in self.web_url:
            body['webhooks'] = [{'base_url': f'{self.web_url}/api/v1/webhooks'}]
        else:
            _logger.warning(
                'No publicly reachable OH_WEB_URL is configured, so E2B sandboxes '
                'cannot post events back to the app server. Conversations will '
                'start but will not receive agent events.'
            )

        return body

    async def resume_sandbox(self, sandbox_id: str) -> bool:
        """Resume a paused sandbox.

        The session API key is unchanged across pause and resume: the sandbox
        keeps its id and host, and the agent server process is restored from
        the memory snapshot holding the key it was claimed with, so there is
        nothing to re-issue.

        A sandbox reports ``paused`` before its memory snapshot is placeable, so
        a resume that closely follows a pause is rejected for a second or so.
        The retry loop covers that window - a user who pauses a conversation and
        immediately resumes it would otherwise be told the sandbox is gone.
        """
        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        for attempt in range(1, self.resume_retries + 1):
            try:
                # E2B has no resume(); connecting to a paused sandbox resumes
                # it, and connecting to a running one is a no-op.
                await AsyncSandbox.connect(
                    sandbox_id, timeout=self.timeout_seconds, **self._api_params
                )
                return True
            except AuthenticationException as exc:
                raise _auth_error(exc) from exc
            except SandboxNotFoundException:
                return False
            except SandboxException as exc:
                if attempt == self.resume_retries:
                    _logger.exception(
                        f'Error resuming sandbox {sandbox_id}', stack_info=True
                    )
                    return False
                _logger.info(
                    f'Retrying resume of sandbox {sandbox_id} after {exc}',
                )
                await asyncio.sleep(self.resume_retry_interval)
        return False

    async def pause_sandbox(self, sandbox_id: str) -> bool:
        """Pause a running sandbox.

        The stored key is kept. ``auto_resume`` wakes the sandbox on the next
        inbound request without going through ``resume_sandbox``, so the key
        has to keep resolving.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        info = await self._get_info(sandbox_id)
        if info is None:
            return False
        if info.state == SandboxState.PAUSED:
            return True
        try:
            # A False result means the sandbox was already paused, which the
            # caller asked for either way.
            await AsyncSandbox.pause(sandbox_id, **self._api_params)
        except AuthenticationException as exc:
            raise _auth_error(exc) from exc
        except SandboxException:
            _logger.exception(f'Error pausing sandbox {sandbox_id}', stack_info=True)
            return False
        return True

    async def delete_sandbox(self, sandbox_id: str) -> bool:
        """Delete a sandbox and its row.

        Returns False only when there is no such sandbox or the caller may not
        see it. A transient E2B failure raises ``SandboxDeleteRetryError`` and
        keeps the row, so a live sandbox is never reported as gone.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        try:
            # A False result means the sandbox was already gone.
            await AsyncSandbox.kill(sandbox_id, **self._api_params)
        except AuthenticationException as exc:
            raise _auth_error(exc) from exc
        except SandboxNotFoundException:
            # E2B reaped it already. Remove the row rather than asking the
            # caller to retry a delete that has nothing left to delete.
            _logger.info(f'Sandbox {sandbox_id} already gone at E2B; removing row')
        except SandboxException as exc:
            _logger.exception(f'Error deleting sandbox {sandbox_id}', stack_info=True)
            raise SandboxDeleteRetryError(
                f'Could not complete delete for sandbox {sandbox_id}: {exc}'
            ) from exc
        await self.db_session.delete(stored_sandbox)
        _vscode_urls.pop(sandbox_id, None)
        return True


class E2BSandboxServiceInjector(SandboxServiceInjector):
    """Dependency injector for E2B sandbox services."""

    api_key: str = Field(
        default_factory=lambda: os.getenv('E2B_API_KEY', ''),
        description='The API key for E2B. Defaults to the E2B_API_KEY env var.',
    )
    domain: str = Field(
        default_factory=lambda: os.getenv('E2B_DOMAIN', 'e2b.app'),
        description=(
            'The E2B domain sandbox ports are exposed under, as '
            'https://{port}-{sandbox_id}.{domain}. Defaults to the E2B_DOMAIN '
            'env var.'
        ),
    )
    api_url: str | None = Field(
        default_factory=lambda: os.getenv('E2B_API_URL'),
        description=(
            'The E2B control plane URL, for self hosted clusters. Defaults to '
            'the E2B_API_URL env var, then to https://api.{domain}.'
        ),
    )
    # 3600 is a safe default rather than a limit. The ceiling is set by the
    # plan - one hour on Hobby, 24 on Pro - and on a self hosted cluster by
    # whatever the operator configured, which is where `400: Timeout cannot be
    # greater than 1 hours` comes from. Raise it where the plan allows.
    timeout_seconds: int = Field(
        default=3600,
        description=(
            'Sandbox lifetime in seconds, measured from the last create or '
            'resume. On expiry the sandbox is paused rather than killed. The '
            'ceiling is set by the E2B plan, or by the operator on a self '
            'hosted cluster.'
        ),
    )
    max_num_sandboxes: int = Field(
        default=10,
        description='Maximum number of sandboxes allowed to run simultaneously',
    )
    init_timeout_seconds: int = Field(
        default=120,
        description=(
            'The max time to wait for a new sandbox to reach the dormant state '
            'before its start is considered failed.'
        ),
    )
    init_poll_interval: float = Field(
        default=1.0,
        description='Seconds between polls while waiting for a dormant agent server',
    )
    resume_retries: int = Field(
        default=5,
        description=(
            'How many times to attempt a resume. A sandbox reports paused '
            'before its memory snapshot is placeable, so a resume that closely '
            'follows a pause needs a retry.'
        ),
    )
    resume_retry_interval: float = Field(
        default=1.0,
        description='Seconds between resume attempts',
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
        async with (
            get_user_context(state, request) as user_context,
            get_httpx_client(state, request) as httpx_client,
            get_sandbox_spec_service(state, request) as sandbox_spec_service,
            get_db_session(state, request) as db_session,
        ):
            yield E2BSandboxService(
                sandbox_spec_service=sandbox_spec_service,
                user_context=user_context,
                httpx_client=httpx_client,
                db_session=db_session,
                api_key=self.api_key,
                domain=self.domain,
                api_url=self.api_url,
                timeout_seconds=self.timeout_seconds,
                max_num_sandboxes=self.max_num_sandboxes,
                init_timeout_seconds=self.init_timeout_seconds,
                init_poll_interval=self.init_poll_interval,
                resume_retries=self.resume_retries,
                resume_retry_interval=self.resume_retry_interval,
                web_url=config.web_url,
                permitted_cors_origins=config.permitted_cors_origins,
            )
