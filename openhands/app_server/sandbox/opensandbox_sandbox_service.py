import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from posixpath import dirname
from typing import Any, AsyncGenerator, ClassVar

import base62
import httpx
from fastapi import Request
from opensandbox import Sandbox, SandboxManager
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxApiException, SandboxException
from opensandbox.models.sandboxes import SandboxFilter, SandboxState
from opensandbox.models.sandboxes import SandboxInfo as OpenSandboxInfo
from pydantic import Field, SecretStr

from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
from openhands.app_server.sandbox.managed_sandbox_service import (
    ManagedSandboxService,
    ManagedSandboxServiceInjector,
    ProviderOutcome,
)
from openhands.app_server.sandbox.opensandbox_sandbox_spec_service import (
    OpenSandboxSandboxSpecInfo,
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
    OPENSANDBOX_BACKEND,
    StoredSandbox,
    get_credential_org_id,
    get_stored_sandbox_by_session_api_key,
    hash_session_api_key,
    require_user_id,
    sandbox_started_by,
    search_stored_sandboxes,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import ADMIN

_logger = logging.getLogger(__name__)

# The agent server keeps conversations and bash events here, outside the
# workspace the agent may serve on the app ports (OHE-3812).
STATE_DIR = '/var/openhands'

# Metadata keys that tag managed sandboxes, so that one with no row can be
# found. Ownership itself lives in the sandbox table.
MANAGED_METADATA_KEY = 'oh_managed'
SANDBOX_SPEC_ID_METADATA_KEY = 'oh_spec_id'
CREATED_BY_USER_ID_METADATA_KEY = 'oh_user_id'

WORKER_1_PORT = 8011
WORKER_2_PORT = 8012

STATUS_MAPPING = {
    SandboxState.PENDING: SandboxStatus.STARTING,
    SandboxState.RUNNING: SandboxStatus.RUNNING,
    SandboxState.PAUSING: SandboxStatus.PAUSED,
    SandboxState.PAUSED: SandboxStatus.PAUSED,
    SandboxState.STOPPING: SandboxStatus.MISSING,
    SandboxState.TERMINATED: SandboxStatus.MISSING,
    SandboxState.FAILED: SandboxStatus.ERROR,
}

MISSING_INIT_API_KEY = (
    'has no init API key. The OpenSandbox template boots its agent server '
    'with a static OH_SECRET_KEY, and the app server must be configured with '
    'the same value: set OPENSANDBOX_INIT_API_KEY to the key '
    'scripts/opensandbox/build_template.py printed.'
)


def _as_opensandbox_spec(sandbox_spec: SandboxSpecInfo) -> OpenSandboxSandboxSpecInfo:
    """Narrow a spec to the OpenSandbox shape, filling defaults for a plain spec."""
    if isinstance(sandbox_spec, OpenSandboxSandboxSpecInfo):
        return sandbox_spec
    return OpenSandboxSandboxSpecInfo(**sandbox_spec.model_dump())


def _init_api_key(sandbox_spec: OpenSandboxSandboxSpecInfo) -> str | None:
    """The template's init key, treating an empty value as unset."""
    if sandbox_spec.init_api_key is None:
        return None
    return sandbox_spec.init_api_key.get_secret_value() or None


def _is_not_found(exc: SandboxException) -> bool:
    return isinstance(exc, SandboxApiException) and exc.status_code == 404


@dataclass
class OpenSandboxSandboxService(ManagedSandboxService):
    """Sandbox service backed by OpenSandbox's Fast Sandbox runtime.

    Each sandbox is a Firecracker microVM restored from a template's golden
    snapshot. The template boots the agent server in deferred-init mode, and
    ``start_sandbox`` completes the ``/api/init`` handshake before returning.
    Pause captures memory and disk to the artifact store, and resume may land
    on another node.

    Browsers reach a sandbox port at ``https://{sandbox_id}-{port}.{domain}``.
    The OpenSandbox ingress gateway routes that host, so it must run with
    ``--fast-sandbox-namespace``. The agent server authenticates every request
    with the session API key.
    """

    backend: ClassVar[str] = OPENSANDBOX_BACKEND

    sandbox_spec_service: SandboxSpecService
    api_domain: str
    api_protocol: str
    api_key: str
    sandbox_domain: str
    sandbox_url_scheme: str
    timeout_seconds: int
    ready_timeout_seconds: int
    init_timeout_seconds: int
    init_poll_interval: float
    resume_timeout_seconds: int
    web_url: str | None = None
    webhook_base_url: str | None = None
    permitted_cors_origins: list[str] = field(default_factory=list)

    def _connection_config(self) -> ConnectionConfig:
        return ConnectionConfig(
            domain=self.api_domain,
            protocol=self.api_protocol,
            api_key=self.api_key,
        )

    @asynccontextmanager
    async def _manager(self) -> AsyncIterator[SandboxManager]:
        manager = await SandboxManager.create(self._connection_config())
        try:
            yield manager
        finally:
            await manager.close()

    async def _get_info(self, sandbox_id: str) -> OpenSandboxInfo | None:
        """OpenSandbox's info for a sandbox, or None when it has no such one."""
        async with self._manager() as manager:
            try:
                return await manager.get_sandbox_info(sandbox_id)
            except SandboxException as exc:
                if _is_not_found(exc):
                    return None
                raise SandboxError(
                    f'Could not read sandbox {sandbox_id}: {exc}'
                ) from exc

    async def _live_infos(self, wanted_ids: set[str]) -> dict[str, OpenSandboxInfo]:
        """Live state for the sandboxes on a page, indexed by id."""
        if not wanted_ids:
            return {}
        metadata = {MANAGED_METADATA_KEY: 'true'}
        if self.user_context != ADMIN:
            metadata[CREATED_BY_USER_ID_METADATA_KEY] = await require_user_id(
                self.user_context
            )
        found: dict[str, OpenSandboxInfo] = {}
        page = 1
        async with self._manager() as manager:
            while wanted_ids - found.keys():
                try:
                    result = await manager.list_sandbox_infos(
                        SandboxFilter(metadata=metadata, page=page, page_size=100)
                    )
                except SandboxException as exc:
                    raise SandboxError(f'Could not list sandboxes: {exc}') from exc
                for item in result.sandbox_infos:
                    if item.id in wanted_ids:
                        found[item.id] = item
                if not result.pagination.has_next_page:
                    break
                page += 1
        return found

    async def _get_spec(self, sandbox_spec_id: str) -> OpenSandboxSandboxSpecInfo:
        """The spec a sandbox was created from, or the default shape."""
        sandbox_spec = await self.sandbox_spec_service.get_sandbox_spec(sandbox_spec_id)
        if sandbox_spec is None:
            return OpenSandboxSandboxSpecInfo(id=sandbox_spec_id, command=None)
        return _as_opensandbox_spec(sandbox_spec)

    def _host_url(self, sandbox_id: str, port: int) -> str:
        return f'{self.sandbox_url_scheme}://{sandbox_id}-{port}.{self.sandbox_domain}'

    def _exposed_urls(
        self,
        sandbox_id: str,
        sandbox_spec: OpenSandboxSandboxSpecInfo,
        session_api_key: str,
    ) -> list[ExposedUrl]:
        # The agent server gives VSCode the session API key as its connection
        # token on POST /api/init.
        vscode_url = (
            f'{self._host_url(sandbox_id, sandbox_spec.vscode_port)}'
            f'/?tkn={session_api_key}&folder={sandbox_spec.working_dir}'
        )
        return [
            ExposedUrl(
                name=AGENT_SERVER,
                url=self._host_url(sandbox_id, sandbox_spec.agent_server_port),
                port=sandbox_spec.agent_server_port,
            ),
            ExposedUrl(
                name=WORKER_1,
                url=self._host_url(sandbox_id, WORKER_1_PORT),
                port=WORKER_1_PORT,
            ),
            ExposedUrl(
                name=WORKER_2,
                url=self._host_url(sandbox_id, WORKER_2_PORT),
                port=WORKER_2_PORT,
            ),
            ExposedUrl(name=VSCODE, url=vscode_url, port=sandbox_spec.vscode_port),
        ]

    async def _to_sandbox_info(
        self,
        stored_sandbox: StoredSandbox,
        info: OpenSandboxInfo | None,
        session_api_key: str | None = None,
    ) -> SandboxInfo:
        """A SandboxInfo from the stored row plus its live OpenSandbox state."""
        status = (
            SandboxStatus.MISSING
            if info is None
            else STATUS_MAPPING.get(info.status.state, SandboxStatus.ERROR)
        )

        exposed_urls = None
        if status == SandboxStatus.RUNNING and session_api_key:
            sandbox_spec = await self._get_spec(stored_sandbox.sandbox_spec_id)
            exposed_urls = self._exposed_urls(
                stored_sandbox.id, sandbox_spec, session_api_key
            )
        else:
            session_api_key = None

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
        )

    @staticmethod
    def _raw_key(stored_sandbox: StoredSandbox) -> str | None:
        key = stored_sandbox.session_api_key
        return key.get_secret_value() if key else None

    async def search_sandboxes(
        self,
        page_id: str | None = None,
        limit: int = 100,
    ) -> SandboxPage:
        page = await search_stored_sandboxes(
            self.db_session, self.user_context, OPENSANDBOX_BACKEND, page_id, limit
        )
        infos = await self._live_infos({row.id for row in page.items})
        sandboxes = await asyncio.gather(
            *[
                self._to_sandbox_info(
                    stored_sandbox,
                    infos.get(stored_sandbox.id),
                    self._raw_key(stored_sandbox),
                )
                for stored_sandbox in page.items
            ]
        )
        return SandboxPage(items=list(sandboxes), next_page_id=page.next_page_id)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
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
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, OPENSANDBOX_BACKEND, session_api_key
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
        """Sandbox identity by session API key, with no OpenSandbox call."""
        stored_sandbox = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, OPENSANDBOX_BACKEND, session_api_key
        )
        if stored_sandbox is None:
            return None
        return SandboxRecord(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
            credential_org_id=stored_sandbox.credential_org_id,
        )

    async def start_sandbox(
        self,
        sandbox_spec_id: str | None = None,
        sandbox_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        started_by: SandboxStartedBy = SandboxStartedBy.SANDBOX_API,
    ) -> SandboxInfo:
        """Restore a sandbox from its template and initialize its agent server.

        ``sandbox_id`` is ignored: OpenSandbox assigns the id.
        """
        user_id = await require_user_id(self.user_context)
        credential_org_id = await get_credential_org_id(self.user_context)

        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        user_default_spec_id = await self.user_context.get_default_sandbox_spec_id()
        sandbox_spec = _as_opensandbox_spec(
            await resolve_sandbox_spec(
                sandbox_spec_id,
                user_default_spec_id,
                self.sandbox_spec_service,
                _logger,
            )
        )
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
            sandbox = await Sandbox.create_from_template(
                sandbox_spec.id,
                timeout=timedelta(seconds=self.timeout_seconds),
                ready_timeout=timedelta(seconds=self.ready_timeout_seconds),
                metadata=metadata,
                connection_config=self._connection_config(),
                # The agent server's init state is the readiness signal.
                skip_health_check=True,
            )
        except SandboxException as exc:
            _logger.exception('Failed to create sandbox', stack_info=True)
            raise SandboxError(f'Failed to start sandbox: {exc}') from exc
        new_sandbox_id = sandbox.id
        await sandbox.close()

        session_api_key = base62.encodebytes(os.urandom(32))
        stored_sandbox = self._new_stored_sandbox(
            id=new_sandbox_id,
            created_by_user_id=user_id,
            credential_org_id=credential_org_id,
            started_by=started_by.value,
            sandbox_spec_id=sandbox_spec.id,
            session_api_key_hash=hash_session_api_key(session_api_key),
            session_api_key=SecretStr(session_api_key),
        )
        try:
            self.db_session.add(stored_sandbox)
            await self.db_session.flush()
            await self._initialize_agent_server(
                new_sandbox_id, sandbox_spec, session_api_key, extra_env=extra_env
            )
        except BaseException:
            # Init is never retried: the rotated secret_key makes a second
            # attempt answer 401. A restore is fast, so a failed sandbox is
            # thrown away.
            await self._kill_quietly(new_sandbox_id)
            raise

        return SandboxInfo(
            id=new_sandbox_id,
            created_by_user_id=user_id,
            credential_org_id=credential_org_id,
            started_by=started_by,
            sandbox_spec_id=sandbox_spec.id,
            status=SandboxStatus.RUNNING,
            session_api_key=session_api_key,
            exposed_urls=self._exposed_urls(
                new_sandbox_id, sandbox_spec, session_api_key
            ),
            created_at=stored_sandbox.created_at,
        )

    async def _kill_quietly(self, sandbox_id: str) -> None:
        try:
            async with self._manager() as manager:
                await manager.kill_sandbox(sandbox_id)
        except Exception:
            _logger.warning(f'Could not kill sandbox {sandbox_id} after a failed init')

    async def _initialize_agent_server(
        self,
        sandbox_id: str,
        sandbox_spec: OpenSandboxSandboxSpecInfo,
        session_api_key: str,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        """Wait for the dormant agent server and hand it its runtime config."""
        agent_server_url = self._host_url(sandbox_id, sandbox_spec.agent_server_port)
        await self._wait_for_dormant(agent_server_url)

        init_api_key = _init_api_key(sandbox_spec)
        headers = {'X-Init-API-Key': init_api_key} if init_api_key else {}
        try:
            response = await self.httpx_client.post(
                f'{agent_server_url}/api/init',
                json=self._build_init_request(sandbox_spec, session_api_key, extra_env),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise SandboxError(
                f'Could not reach the agent server for sandbox {sandbox_id}'
            ) from exc
        if response.status_code != 200:
            _logger.error(
                f'Agent server init for sandbox {sandbox_id} returned '
                f'{response.status_code}: {response.text}'
            )
            if response.status_code == 401:
                raise SandboxError(
                    f'The agent server in sandbox {sandbox_id} rejected the init '
                    'API key. It must match the OH_SECRET_KEY baked into '
                    f'template {sandbox_spec.id!r}.'
                )
            raise SandboxError(f'Failed to initialize sandbox {sandbox_id}')

    async def _wait_for_dormant(self, agent_server_url: str) -> None:
        """Poll ``GET /api/init`` until the agent server is ready to be claimed."""
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
        self,
        sandbox_spec: OpenSandboxSandboxSpecInfo,
        session_api_key: str,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """The ``POST /api/init`` body. ``secret_key`` is rotated per sandbox."""
        working_dir = sandbox_spec.working_dir.rstrip('/')
        workspace_dir = dirname(working_dir)
        if workspace_dir in ('', '/'):
            workspace_dir = working_dir
        body: dict[str, Any] = {
            'session_api_keys': [session_api_key],
            'secret_key': base62.encodebytes(os.urandom(32)),
            'conversations_path': f'{STATE_DIR}/conversations',
            'bash_events_dir': f'{STATE_DIR}/bash_events',
            'conversation_worktree_root': f'{workspace_dir}/worktrees',
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
        if webhook_base_url:
            body['webhooks'] = [{'base_url': f'{webhook_base_url}/api/v1/webhooks'}]
        return body

    async def _wait_for_state(self, sandbox_id: str, state: str) -> bool:
        deadline = time.monotonic() + self.resume_timeout_seconds
        while time.monotonic() < deadline:
            info = await self._get_info(sandbox_id)
            if info is None or info.status.state == SandboxState.FAILED:
                return False
            if info.status.state == state:
                return True
            await asyncio.sleep(0.2)
        return False

    async def _resume_at_provider(
        self, stored_sandbox: StoredSandbox
    ) -> ProviderOutcome:
        """Resume the microVM from its checkpoint, possibly on another node.

        The agent server comes back from the memory snapshot holding the same
        session API key, so nothing is re-issued.
        """
        sandbox_id = stored_sandbox.id
        info = await self._get_info(sandbox_id)
        if info is None:
            return ProviderOutcome.FAILED
        state = info.status.state
        if state == SandboxState.RUNNING:
            return ProviderOutcome.ALREADY_DONE
        if state == SandboxState.PAUSING:
            if not await self._wait_for_state(sandbox_id, SandboxState.PAUSED):
                return ProviderOutcome.FAILED
        elif state != SandboxState.PAUSED:
            return ProviderOutcome.SKIPPED
        try:
            async with self._manager() as manager:
                await manager.resume_sandbox(sandbox_id)
        except SandboxException:
            _logger.exception(f'Error resuming sandbox {sandbox_id}', stack_info=True)
            return ProviderOutcome.FAILED
        if not await self._wait_for_state(sandbox_id, SandboxState.RUNNING):
            _logger.error(f'Sandbox {sandbox_id} did not resume')
            return ProviderOutcome.FAILED
        return ProviderOutcome.CHANGED

    async def _pause_at_provider(
        self, stored_sandbox: StoredSandbox
    ) -> ProviderOutcome:
        """Pause the microVM. OpenSandbox checkpoints memory and disk."""
        sandbox_id = stored_sandbox.id
        info = await self._get_info(sandbox_id)
        if info is None:
            return ProviderOutcome.FAILED
        state = info.status.state
        if state in (SandboxState.PAUSED, SandboxState.PAUSING):
            return ProviderOutcome.ALREADY_DONE
        if state != SandboxState.RUNNING:
            return ProviderOutcome.SKIPPED
        try:
            async with self._manager() as manager:
                await manager.pause_sandbox(sandbox_id)
        except SandboxException:
            _logger.exception(f'Error pausing sandbox {sandbox_id}', stack_info=True)
            return ProviderOutcome.FAILED
        return ProviderOutcome.CHANGED

    async def _delete_at_provider(self, stored_sandbox: StoredSandbox) -> None:
        sandbox_id = stored_sandbox.id
        try:
            async with self._manager() as manager:
                await manager.kill_sandbox(sandbox_id)
        except SandboxException as exc:
            if _is_not_found(exc):
                _logger.info(f'Sandbox {sandbox_id} already gone; removing row')
                return
            _logger.exception(f'Error deleting sandbox {sandbox_id}', stack_info=True)
            raise SandboxDeleteRetryError(
                f'Could not complete delete for sandbox {sandbox_id}: {exc}'
            ) from exc


class OpenSandboxSandboxServiceInjector(ManagedSandboxServiceInjector):
    """Dependency injector for OpenSandbox sandbox services."""

    backend: ClassVar[str] = OPENSANDBOX_BACKEND

    api_domain: str = Field(
        default_factory=lambda: os.getenv('OPENSANDBOX_DOMAIN', ''),
        description=(
            'Host and port of the OpenSandbox lifecycle server, for example '
            'opensandbox-server.opensandbox-system.svc:80. Defaults to the '
            'OPENSANDBOX_DOMAIN env var.'
        ),
    )
    api_protocol: str = Field(
        default_factory=lambda: os.getenv('OPENSANDBOX_PROTOCOL', 'http'),
        description='http or https, for the lifecycle server.',
    )
    api_key: str = Field(
        default_factory=lambda: os.getenv('OPENSANDBOX_API_KEY', ''),
        description='The lifecycle server API key. Defaults to OPENSANDBOX_API_KEY.',
    )
    sandbox_domain: str = Field(
        default_factory=lambda: os.getenv('OPENSANDBOX_SANDBOX_DOMAIN', ''),
        description=(
            'The wildcard domain the ingress gateway serves sandbox ports under, '
            'as {sandbox_id}-{port}.{domain}. Defaults to '
            'OPENSANDBOX_SANDBOX_DOMAIN.'
        ),
    )
    sandbox_url_scheme: str = Field(
        default_factory=lambda: os.getenv('OPENSANDBOX_SANDBOX_URL_SCHEME', 'https'),
        description='The scheme of sandbox URLs.',
    )
    webhook_base_url: str | None = Field(
        default_factory=lambda: os.getenv('OPENSANDBOX_WEBHOOK_BASE_URL'),
        description=(
            'Base URL sandboxes post events to. Defaults to OH_WEB_URL. Set it '
            'when sandboxes reach the app server at a different address.'
        ),
    )
    timeout_seconds: int = Field(
        default=30 * 24 * 3600,
        description=(
            'Sandbox lifetime. OpenSandbox requires one in template mode; the '
            "app's lifecycle rules delete sandboxes well before it."
        ),
    )
    max_num_sandboxes: int = Field(
        default=10,
        description='Maximum number of sandboxes allowed to run simultaneously',
    )
    ready_timeout_seconds: int = Field(
        default=60,
        description='How long to wait for OpenSandbox to publish a new sandbox.',
    )
    init_timeout_seconds: int = Field(
        default=120,
        description='How long to wait for a new agent server to reach dormant.',
    )
    init_poll_interval: float = Field(
        default=0.5,
        description='Seconds between polls while waiting for a dormant agent server',
    )
    resume_timeout_seconds: int = Field(
        default=120,
        description='How long to wait for a paused sandbox to run again.',
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
            yield OpenSandboxSandboxService(
                sandbox_spec_service=sandbox_spec_service,
                user_context=user_context,
                httpx_client=httpx_client,
                db_session=db_session,
                api_domain=self.api_domain,
                api_protocol=self.api_protocol,
                api_key=self.api_key,
                sandbox_domain=self.sandbox_domain,
                sandbox_url_scheme=self.sandbox_url_scheme,
                timeout_seconds=self.timeout_seconds,
                max_num_sandboxes=self.max_num_sandboxes,
                ready_timeout_seconds=self.ready_timeout_seconds,
                init_timeout_seconds=self.init_timeout_seconds,
                init_poll_interval=self.init_poll_interval,
                resume_timeout_seconds=self.resume_timeout_seconds,
                web_url=config.web_url,
                webhook_base_url=self.webhook_base_url,
                permitted_cors_origins=config.permitted_cors_origins,
                lifecycle=self.lifecycle,
            )
