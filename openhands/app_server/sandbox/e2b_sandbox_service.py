import asyncio
import hashlib
import hmac
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
    SandboxException,
    SandboxNotFoundException,
    SandboxQuery,
    SandboxState,
)
from e2b import SandboxInfo as E2BSandboxInfo
from fastapi import Request
from pydantic import Field

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
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user.user_context import UserContext

_logger = logging.getLogger(__name__)

# E2B sandbox metadata is the store for sandbox identity - these keys carry it.
# Metadata filtering is applied server side and multiple keys are AND-ed, so a
# metadata query is the equivalent of the `WHERE` clause other backends run
# against their own database.
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


def _derive_session_api_key(secret: str, e2b_sandbox_id: str) -> str:
    """Derive the session API key for a sandbox from an encryption key.

    The key is a pure function of the sandbox id, so it is recoverable after an
    app server restart with nothing persisted, and a key presented by a client
    identifies its own sandbox without a scan.
    """
    digest = hmac.new(secret.encode(), e2b_sandbox_id.encode(), hashlib.sha256).digest()
    return f'{e2b_sandbox_id}.{base62.encodebytes(digest)}'


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
    handshake before returning. There is no local database: E2B sandbox
    metadata carries ownership and spec identity, and the session API key is
    derived from the sandbox id rather than stored.

    E2B requires a publicly reachable ``OH_WEB_URL`` for agent server event
    callbacks. There is no polling fallback for this backend, so conversations
    started against a localhost app server will not receive events.
    """

    sandbox_spec_service: SandboxSpecService
    user_context: UserContext
    httpx_client: httpx.AsyncClient
    jwt_service: JwtService
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
    # Session API keys
    # ------------------------------------------------------------------

    def _derive_session_api_key(self, e2b_sandbox_id: str) -> str:
        """Derive this sandbox's session API key with the default encryption key."""
        secret = self.jwt_service.get_key(
            self.jwt_service.default_key_id
        ).key.get_secret_value()
        return _derive_session_api_key(secret, e2b_sandbox_id)

    def _sandbox_id_from_session_api_key(self, session_api_key: str) -> str | None:
        """Recover the sandbox id a session API key was derived for.

        The key is verified against every known encryption key, not just the
        default, so rotating the default does not orphan live sandboxes.
        Returns None when the key is malformed or does not verify.
        """
        e2b_sandbox_id, separator, _ = session_api_key.rpartition('.')
        if not separator or not e2b_sandbox_id:
            return None
        for key_id in self.jwt_service.key_ids:
            secret = self.jwt_service.get_key(key_id).key.get_secret_value()
            expected = _derive_session_api_key(secret, e2b_sandbox_id)
            if hmac.compare_digest(expected, session_api_key):
                return e2b_sandbox_id
        return None

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    async def _owned_metadata_filter(self) -> dict[str, str]:
        """Metadata filter narrowing a lookup to what the caller may see.

        A caller with a user id sees only their own sandboxes. A caller without
        one (OSS single user mode, and the admin contexts used by webhook and
        session key auth) sees every managed sandbox.
        """
        metadata = {MANAGED_METADATA_KEY: 'true'}
        user_id = await self.user_context.get_user_id()
        if user_id:
            metadata[CREATED_BY_USER_ID_METADATA_KEY] = user_id
        return metadata

    async def _is_owned(self, info: E2BSandboxInfo) -> bool:
        """Whether the caller may see this sandbox."""
        metadata = info.metadata or {}
        if metadata.get(MANAGED_METADATA_KEY) != 'true':
            return False
        user_id = await self.user_context.get_user_id()
        if user_id and metadata.get(CREATED_BY_USER_ID_METADATA_KEY) != user_id:
            return False
        return True

    async def _get_owned_info(self, sandbox_id: str) -> E2BSandboxInfo | None:
        """Get E2B's info for a sandbox, or None when the caller may not see it."""
        try:
            info = await AsyncSandbox.get_info(sandbox_id, **self._api_params)
        except SandboxNotFoundException:
            return None
        except SandboxException as exc:
            # A malformed id is rejected by the API with 400 Invalid sandbox ID.
            _logger.debug(f'Sandbox lookup failed for {sandbox_id}: {exc}')
            return None
        if not await self._is_owned(info):
            return None
        return info

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
        vscode_url = await self._resolve_vscode_url(
            e2b_sandbox_id, sandbox_spec, session_api_key
        )
        if vscode_url:
            exposed_urls.append(
                ExposedUrl(name=VSCODE, url=vscode_url, port=sandbox_spec.vscode_port)
            )
        return exposed_urls

    async def _to_sandbox_info(self, info: E2BSandboxInfo) -> SandboxInfo:
        metadata = info.metadata or {}
        status = STATUS_MAPPING.get(info.state, SandboxStatus.ERROR)
        sandbox_spec_id = metadata.get(SANDBOX_SPEC_ID_METADATA_KEY, '')

        session_api_key = None
        exposed_urls = None
        if status == SandboxStatus.RUNNING:
            session_api_key = self._derive_session_api_key(info.sandbox_id)
            sandbox_spec = await self._get_spec(sandbox_spec_id)
            exposed_urls = await self._exposed_urls(
                info.sandbox_id, sandbox_spec, session_api_key
            )

        return SandboxInfo(
            id=info.sandbox_id,
            created_by_user_id=metadata.get(CREATED_BY_USER_ID_METADATA_KEY),
            sandbox_spec_id=sandbox_spec_id,
            status=status,
            session_api_key=session_api_key,
            exposed_urls=exposed_urls,
            created_at=info.started_at,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def search_sandboxes(
        self,
        page_id: str | None = None,
        limit: int = 100,
    ) -> SandboxPage:
        """Search for sandboxes.

        Paused sandboxes are requested explicitly: E2B's default list shows
        running sandboxes only, and a paused sandbox is a conversation the user
        can still resume.
        """
        query = SandboxQuery(
            metadata=await self._owned_metadata_filter(),
            state=[SandboxState.RUNNING, SandboxState.PAUSED],
        )
        # `list()` is not a coroutine - it returns a paginator synchronously.
        paginator = AsyncSandbox.list(
            query=query, limit=limit, next_token=page_id, **self._api_params
        )
        try:
            items = await paginator.next_items()
        except SandboxException:
            _logger.exception('Error listing sandboxes', stack_info=True)
            return SandboxPage(items=[], next_page_id=None)
        sandboxes = [await self._to_sandbox_info(item) for item in items]
        return SandboxPage(items=sandboxes, next_page_id=paginator.next_token)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        """Get a single sandbox."""
        info = await self._get_owned_info(sandbox_id)
        if info is None:
            return None
        return await self._to_sandbox_info(info)

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        """Get a single sandbox by session API key.

        The key carries the sandbox id it was derived for, so this is a direct
        lookup rather than a scan.
        """
        e2b_sandbox_id = self._sandbox_id_from_session_api_key(session_api_key)
        if e2b_sandbox_id is None:
            return None
        return await self.get_sandbox(e2b_sandbox_id)

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        """Get sandbox identity by session API key.

        Unlike backends with their own database this still costs one E2B call,
        because ownership lives in sandbox metadata - but it skips the VSCode
        URL resolution that ``get_sandbox_by_session_api_key`` performs.
        """
        e2b_sandbox_id = self._sandbox_id_from_session_api_key(session_api_key)
        if e2b_sandbox_id is None:
            return None
        info = await self._get_owned_info(e2b_sandbox_id)
        if info is None:
            return None
        return SandboxRecord(
            id=info.sandbox_id,
            created_by_user_id=(info.metadata or {}).get(
                CREATED_BY_USER_ID_METADATA_KEY
            ),
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

        user_id = await self.user_context.get_user_id()
        metadata = {
            MANAGED_METADATA_KEY: 'true',
            SANDBOX_SPEC_ID_METADATA_KEY: sandbox_spec.id,
        }
        if user_id:
            metadata[CREATED_BY_USER_ID_METADATA_KEY] = user_id

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
        except SandboxException as exc:
            _logger.exception('Failed to create sandbox', stack_info=True)
            raise SandboxError('Failed to start sandbox') from exc

        e2b_sandbox_id = sandbox.sandbox_id
        session_api_key = self._derive_session_api_key(e2b_sandbox_id)
        try:
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
            created_at=utc_now(),
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
        keeps its id and host, and the agent server process is restored from the
        memory snapshot, so there is nothing to re-derive or invalidate.

        A sandbox reports ``paused`` before its memory snapshot is placeable, so
        a resume that closely follows a pause is rejected for a second or so.
        The retry loop covers that window - a user who pauses a conversation and
        immediately resumes it would otherwise be told the sandbox is gone.
        """
        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        info = await self._get_owned_info(sandbox_id)
        if info is None:
            return False
        for attempt in range(1, self.resume_retries + 1):
            try:
                # E2B has no resume(); connecting to a paused sandbox resumes
                # it, and connecting to a running one is a no-op.
                await AsyncSandbox.connect(
                    sandbox_id, timeout=self.timeout_seconds, **self._api_params
                )
                return True
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
        """Pause a running sandbox."""
        info = await self._get_owned_info(sandbox_id)
        if info is None:
            return False
        if info.state == SandboxState.PAUSED:
            return True
        try:
            # A False result means the sandbox was already paused, which the
            # caller asked for either way.
            await AsyncSandbox.pause(sandbox_id, **self._api_params)
        except SandboxException:
            _logger.exception(f'Error pausing sandbox {sandbox_id}', stack_info=True)
            return False
        return True

    async def delete_sandbox(self, sandbox_id: str) -> bool:
        """Delete a sandbox.

        Returns False only when the sandbox does not exist or the caller may not
        see it. A transient E2B failure raises ``SandboxDeleteRetryError`` so a
        live sandbox is never reported as gone.
        """
        info = await self._get_owned_info(sandbox_id)
        if info is None:
            return False
        try:
            # A False result means the sandbox was already gone.
            await AsyncSandbox.kill(sandbox_id, **self._api_params)
        except SandboxException as exc:
            _logger.exception(f'Error deleting sandbox {sandbox_id}', stack_info=True)
            raise SandboxDeleteRetryError(
                f'Could not complete delete for sandbox {sandbox_id}: {exc}'
            ) from exc
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
    # 3600 is E2B's hard server-side ceiling, not a preference: anything above
    # it - 3601 included - is rejected with `400: Timeout cannot be greater
    # than 1 hours`. Configuring a longer lease is not available as a way to
    # keep a long conversation alive.
    timeout_seconds: int = Field(
        default=3600,
        description=(
            'Sandbox lifetime in seconds, measured from the last create or '
            'resume. On expiry the sandbox is paused rather than killed. E2B '
            'caps this at one hour.'
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
            get_global_config,
            get_httpx_client,
            get_jwt_service,
            get_sandbox_spec_service,
            get_user_context,
        )

        config = get_global_config()
        async with (
            get_user_context(state, request) as user_context,
            get_httpx_client(state, request) as httpx_client,
            get_sandbox_spec_service(state, request) as sandbox_spec_service,
            get_jwt_service(state, request) as jwt_service,
        ):
            yield E2BSandboxService(
                sandbox_spec_service=sandbox_spec_service,
                user_context=user_context,
                httpx_client=httpx_client,
                jwt_service=jwt_service,
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
