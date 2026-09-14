"""Multiuser Docker provider with durable ownership and deferred initialization."""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Annotated, NotRequired, ParamSpec, TypedDict, TypeVar, Unpack
from urllib.parse import quote
from uuid import uuid4

import docker
import httpx
from docker.errors import NotFound
from docker.models.containers import Container
from docker.models.volumes import Volume
from fastapi import Request
from pydantic import BaseModel, Field, PrivateAttr, SecretStr, TypeAdapter
from sqlalchemy.exc import IntegrityError

from openhands.agent_server.init_router import InitRequest, InitState, InitStatus
from openhands.agent_server.models import ConversationPage
from openhands.agent_server.utils import utc_now
from openhands.app_server.errors import (
    PermissionsError,
    SandboxDeleteRetryError,
    SandboxError,
)
from openhands.app_server.sandbox import workspace_archive
from openhands.app_server.sandbox.configured_sandbox_spec_service import (
    ConfiguredSandboxSpecService,
)
from openhands.app_server.sandbox.docker_response_models import (
    DockerContainerInspect,
    DockerContainerNames,
    DockerResourceAttributes,
    DockerWaitResult,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    ExposedUrl,
    SandboxInfo,
    SandboxPage,
    SandboxRecord,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_provider_config import (
    DockerLaunchSpec,
    SandboxProviderConfig,
)
from openhands.app_server.sandbox.sandbox_service import (
    SandboxService,
    SandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_spec_service import resolve_sandbox_spec
from openhands.app_server.sandbox.sql_sandbox_store import (
    SQLSandboxStore,
    StoredManagedSandbox,
    hash_session_key,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.settings.settings_models import grouped_workspace_dir
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)

_logger = logging.getLogger(__name__)
T = TypeVar('T')
P = ParamSpec('P')
_CORS_ORIGINS: TypeAdapter[list[str] | int] = TypeAdapter(
    list[str] | Annotated[int, Field(ge=0)]
)
MANAGED_LABEL = 'org.openhands.managed'
APPLICATION_LABEL = 'org.openhands.application'
SANDBOX_LABEL = 'org.openhands.sandbox'
RESOURCE_LABEL = 'org.openhands.resource'
ROLE_LABEL = 'org.openhands.role'

# Runs only in a short-lived, tracked helper with the owned volume mounted.
# Positional arguments prevent operator paths/usernames becoming shell code.
_VOLUME_SETUP = """set -eu
identity="$1"; target="$2"
uid="${identity%%:*}"
if [ "$identity" = "$uid" ]; then gid="$(id -g "$uid" 2>/dev/null || echo 0)"; else gid="${identity#*:}"; fi
case "$uid" in *[!0-9]*) uid="$(id -u "$uid")";; esac
shift 2
mkdir -p "$target" "$@"
chown -R "$uid:$gid" "$target"
"""


class ForeignDockerResource(SandboxError):
    def __init__(self) -> None:
        super().__init__('Docker resource ownership does not match the sandbox record')


class _InitWebhook(TypedDict):
    base_url: str


class _InitPayload(TypedDict):
    session_api_keys: list[str]
    secret_key: str
    conversations_path: str
    bash_events_dir: str
    conversation_worktree_root: str
    allow_cors_origins: list[str]
    web_url: str
    webhooks: NotRequired[list[_InitWebhook]]


class _InjectorOptions(TypedDict, total=False):
    max_num_sandboxes: int
    start_sandbox_timeout: int


@dataclass
class ManagedDockerSandboxService(SandboxService):
    sandbox_spec_service: ConfiguredSandboxSpecService
    store: SQLSandboxStore
    user_context: UserContext
    httpx_client: httpx.AsyncClient = field(repr=False)
    default_sandbox_spec_id: str
    max_num_sandboxes: int = 5
    start_sandbox_timeout: float = 120
    poll_interval: float = 0.5
    web_url: str | None = None
    permitted_cors_origins: list[str] = field(default_factory=list)
    docker_client: docker.DockerClient | None = field(default=None, repr=False)

    async def _docker(
        self, call: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        # Do not release an operation lock while a cancelled thread is still
        # creating resources. Its deterministic handles are already committed.
        task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _client(self) -> docker.DockerClient:
        if self.docker_client is None:
            self.docker_client = await self._docker(docker.from_env, timeout=20)
        return self.docker_client

    @staticmethod
    def _labels(row: StoredManagedSandbox, role: str = 'sandbox') -> dict[str, str]:
        return {
            MANAGED_LABEL: 'true',
            APPLICATION_LABEL: row.application_id,
            SANDBOX_LABEL: row.id,
            RESOURCE_LABEL: row.resource_id,
            ROLE_LABEL: role,
        }

    def _check_labels(
        self, resource: Container | Volume, row: StoredManagedSandbox, role: str
    ) -> None:
        labels = DockerResourceAttributes.model_validate(
            resource.attrs
        ).ownership_labels()
        if any(
            labels.get(key) != value for key, value in self._labels(row, role).items()
        ):
            raise ForeignDockerResource()

    async def _container(self, row: StoredManagedSandbox) -> Container | None:
        client = await self._client()
        try:
            container = await self._docker(
                client.containers.get, row.container_id or row.container_name
            )
        except NotFound:
            return None
        self._check_labels(container, row, 'sandbox')
        if row.container_id and container.id != row.container_id:
            raise ForeignDockerResource()
        return container

    @staticmethod
    def _generation(container: Container) -> str | None:
        started = DockerContainerInspect.model_validate(
            container.attrs
        ).state.started_at
        if not container.id or started.startswith('0001-') or not started:
            return None
        return f'{container.id}:{started}'

    @staticmethod
    def _state(container: Container) -> str:
        return DockerContainerInspect.model_validate(container.attrs).state.status

    def _urls(
        self, row: StoredManagedSandbox, container: Container, include_key: bool = False
    ) -> list[ExposedUrl]:
        launch, _ = self.store.launch(row)
        bindings = (
            DockerContainerInspect.model_validate(
                container.attrs
            ).network_settings.ports
            or {}
        )
        result = []
        for name, port in launch.docker.ports.items():
            published = bindings.get(f'{port}/tcp')
            if not published:
                raise SandboxError(
                    'Docker has not published all configured sandbox ports'
                )
            host_port = int(published[0].host_port)
            if launch.docker.public_url_pattern:
                url = launch.docker.public_url_pattern.format(
                    resource_id=row.resource_id, container_port=port
                ).rstrip('/')
            else:
                url = launch.docker.container_url_pattern.format(port=host_port).rstrip(
                    '/'
                )
            if name == VSCODE and include_key:
                # SDK 1.47 VSCode captures the startup session token before
                # deferred init. It is unique to this sandbox, durable, and
                # independent from the rotating Agent Server API credential.
                key = launch.initial_env['OH_SESSION_API_KEYS_0'].get_secret_value()
                url += f'/?tkn={quote(key)}&folder={quote(launch.working_dir, safe="")}'
            result.append(ExposedUrl(name=name, port=port, url=url))
        return result

    def _agent_url(self, row: StoredManagedSandbox, container: Container) -> str:
        urls = self._urls(row, container)
        return replace_localhost_hostname_for_docker(
            next(item.url for item in urls if item.name == AGENT_SERVER)
        )

    async def _init_state(self, url: str) -> InitState:
        response = await self.httpx_client.get(f'{url}/api/init', timeout=5)
        response.raise_for_status()
        return InitStatus.model_validate_json(response.content).state

    async def _ready(self, row: StoredManagedSandbox, container: Container) -> bool:
        url = self._agent_url(row, container)
        if await self._init_state(url) != 'ready':
            return False
        response = await self.httpx_client.get(
            f'{url}/api/conversations/search',
            params={'limit': 1},
            headers={
                'X-Session-API-Key': self.store.credentials(
                    row
                ).session_key.get_secret_value()
            },
            timeout=5,
        )
        response.raise_for_status()
        ConversationPage.model_validate_json(response.content)
        return True

    def _info(
        self,
        row: StoredManagedSandbox,
        status: SandboxStatus,
        container: Container | None = None,
    ) -> SandboxInfo:
        running = status == SandboxStatus.RUNNING
        if running and container is None:
            raise SandboxError('Running sandbox has no container')
        return SandboxInfo(
            id=row.id,
            created_by_user_id=row.created_by_user_id,
            sandbox_spec_id=row.sandbox_spec_id,
            status=status,
            session_api_key=self.store.credentials(row).session_key.get_secret_value()
            if running and container is not None
            else None,
            exposed_urls=self._urls(row, container, include_key=True)
            if running and container is not None
            else None,
            created_at=row.created_at,
            working_dir=self.store.launch(row)[0].working_dir,
        )

    async def _observed(
        self, row: StoredManagedSandbox, container: Container | None
    ) -> SandboxInfo:
        if container is None:
            if row.desired_state == 'starting' and row.container_id is None:
                return self._info(row, SandboxStatus.STARTING)
            return self._info(row, SandboxStatus.MISSING)
        self._check_labels(container, row, 'sandbox')
        state = self._state(container)
        if row.desired_state in ('error', 'deleting', 'cleanup'):
            return self._info(row, SandboxStatus.ERROR)
        if state == 'created' and row.desired_state == 'starting':
            return self._info(row, SandboxStatus.STARTING)
        if state in ('paused', 'exited', 'created'):
            return self._info(row, SandboxStatus.PAUSED)
        if state in ('dead', 'removing'):
            return self._info(row, SandboxStatus.ERROR)
        if state == 'restarting':
            return self._info(row, SandboxStatus.STARTING)
        if state != 'running':
            return self._info(row, SandboxStatus.UNKNOWN)
        if (
            row.desired_state != 'running'
            or not row.session_api_key_hash
            or not row.initialized_generation
            or self._generation(container) != row.initialized_generation
        ):
            return self._info(row, SandboxStatus.STARTING)
        if await self._ready(row, container):
            return self._info(row, SandboxStatus.RUNNING, container)
        return self._info(row, SandboxStatus.STARTING)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        row = await self.store.get(sandbox_id)
        if row is None:
            return None
        try:
            return await self._observed(row, await self._container(row))
        except Exception:
            return self._info(row, SandboxStatus.UNKNOWN)

    async def _observe_many(
        self, rows: list[StoredManagedSandbox]
    ) -> dict[str, SandboxInfo]:
        if not rows:
            return {}
        try:
            client = await self._client()
            # Docker applies the filter before enumeration. sparse=True avoids
            # implicit serial inspect calls by docker-py; inspect only our page.
            containers = []
            ids = [row.container_id for row in rows if row.container_id]
            names = [f'^/{row.container_name}$' for row in rows if not row.container_id]
            for field_name, identities in (('id', ids), ('name', names)):
                if identities:
                    containers.extend(
                        await self._docker(
                            client.containers.list,
                            all=True,
                            sparse=True,
                            filters={
                                'label': f'{MANAGED_LABEL}=true',
                                field_name: identities,
                            },
                        )
                    )
            by_id = {container.id: container for container in containers}
            # Sparse Docker results have Names, while inspected results have
            # Name. Container.name only understands the latter.
            by_name = {
                name.lstrip('/'): container
                for container in containers
                for name in DockerContainerNames.model_validate(
                    container.attrs
                ).all_names()
            }
        except Exception:
            return {row.id: self._info(row, SandboxStatus.UNKNOWN) for row in rows}
        semaphore = asyncio.Semaphore(8)

        async def observe(row: StoredManagedSandbox) -> tuple[str, SandboxInfo]:
            async with semaphore:
                try:
                    container = (
                        by_id.get(row.container_id)
                        if row.container_id
                        else by_name.get(row.container_name)
                    )
                    if container is None:
                        # A label-filtered list cannot distinguish native
                        # absence from a resource whose labels were changed.
                        # Confirm through the already owned inventory identity.
                        container = await self._container(row)
                    else:
                        self._check_labels(container, row, 'sandbox')
                        if row.container_id and row.container_id != container.id:
                            raise ForeignDockerResource()
                        try:
                            await self._docker(container.reload)
                        except NotFound:
                            container = None
                    return row.id, await self._observed(row, container)
                except Exception:
                    return row.id, self._info(row, SandboxStatus.UNKNOWN)

        return dict(await asyncio.gather(*(observe(row) for row in rows)))

    async def batch_get_sandboxes(
        self, sandbox_ids: list[str]
    ) -> list[SandboxInfo | None]:
        rows = await self.store.get_many(sandbox_ids)
        infos = await self._observe_many(rows)
        return [infos.get(sandbox_id) for sandbox_id in sandbox_ids]

    async def search_sandboxes(
        self, page_id: str | None = None, limit: int = 100
    ) -> SandboxPage:
        try:
            offset = max(0, int(page_id or '0'))
        except ValueError:
            offset = 0
        limit = max(1, min(limit, 100))
        rows = await self.store.page(offset, limit + 1)
        infos = await self._observe_many(rows[:limit])
        return SandboxPage(
            items=list(infos.values()),
            next_page_id=str(offset + limit) if len(rows) > limit else None,
        )

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        return await self.store.record_by_key(session_api_key)

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        row = await self.store.by_key(session_api_key)
        return await self.get_sandbox(row.id) if row else None

    async def _admit(
        self, owner_id: str, excluding: str | None = None, retain: int | None = None
    ) -> list[str]:
        retain = self.max_num_sandboxes - 1 if retain is None else retain
        candidates = await self.store.active_for_owner(owner_id, excluding)
        paused = []
        for row in candidates[: max(0, len(candidates) - retain)]:
            # This internal path never re-enters cap enforcement.
            async with self.store.lock(f'sandbox:{row.id}'):
                current = await self.store.get(row.id)
                if current and await self._pause(current):
                    paused.append(row.id)
                else:
                    raise SandboxError(
                        'Could not make room for another sandbox. Please retry.',
                        status_code=503,
                    )
        return paused

    async def start_sandbox(
        self, sandbox_spec_id: str | None = None, sandbox_id: str | None = None
    ) -> SandboxInfo:
        owner = await self.user_context.get_user_id()
        if not owner:
            raise PermissionsError(
                'An authenticated user is required to create a sandbox'
            )
        try:
            spec = await resolve_sandbox_spec(
                sandbox_spec_id,
                await self.user_context.get_default_sandbox_spec_id(),
                self.sandbox_spec_service,
                _logger,
            )
            selected = spec.id
            launch = await self.sandbox_spec_service.get_launch_spec(selected)
        except (KeyError, ValueError):
            raise SandboxError(
                'The selected sandbox template is unavailable', status_code=400
            ) from None
        if launch.provider != 'docker':
            raise SandboxError(
                'The selected template is not a Docker template', status_code=400
            )
        sandbox_id = sandbox_id or uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', sandbox_id):
            raise SandboxError('Invalid sandbox ID', status_code=400)
        async with (
            self.store.lock(f'owner:{owner}'),
            self.store.lock(f'sandbox:{sandbox_id}'),
        ):
            existing = await self.store.get(sandbox_id)
            if existing:
                if existing.sandbox_spec_id != selected:
                    raise SandboxError(
                        'Sandbox already exists with another template', status_code=409
                    )
                if not await self._resume(existing):
                    raise SandboxError(
                        'Sandbox allocation is incomplete or its container was removed. '
                        'Delete this sandbox and create a new one.',
                        status_code=409,
                    )
                info = await self.get_sandbox(sandbox_id)
                assert info is not None
                return info
            resource_id = uuid4().hex
            launch = launch.model_copy(deep=True)
            # The SDK passes this as a separate OpenVSCode CLI argument, where
            # a leading '-' is parsed as another option. Hex retains 256 bits.
            service_token = secrets.token_hex(32)
            # The SDK parser uses the canonical list to bound indexed values.
            # Override image-baked lists as well as index 0 so an empty list
            # cannot disable auth and extra static tokens cannot survive.
            launch.initial_env['OH_SESSION_API_KEYS'] = SecretStr(
                json.dumps([service_token])
            )
            launch.initial_env['OH_SESSION_API_KEYS_0'] = SecretStr(service_token)
            template_origins = [
                value.get_secret_value()
                for name, value in sorted(launch.initial_env.items())
                if re.fullmatch(r'OH_ALLOW_CORS_ORIGINS_[0-9]+', name)
            ]
            raw_origins = launch.initial_env.get('OH_ALLOW_CORS_ORIGINS')
            if raw_origins:
                try:
                    values = _CORS_ORIGINS.validate_json(
                        raw_origins.get_secret_value(), strict=True
                    )
                    if isinstance(values, list):
                        template_origins = values + template_origins
                except ValueError:
                    raise SandboxError(
                        'Docker template has invalid CORS origins', status_code=400
                    ) from None
            cors_origins = list(
                dict.fromkeys(
                    template_origins
                    + ([self.web_url] if self.web_url else [])
                    + self.permitted_cors_origins
                )
            )
            session_key, workspace_key = (
                secrets.token_urlsafe(32),
                secrets.token_urlsafe(32),
            )
            # SDK 1.47 constructs cipher/settings stores before deferred init.
            # Docker can inject the durable workspace key before process start,
            # then retain that same key through initialization and restarts.
            launch.initial_env['OH_SECRET_KEY'] = SecretStr(workspace_key)
            row = StoredManagedSandbox(
                id=sandbox_id,
                provider='docker',
                created_by_user_id=owner,
                sandbox_spec_id=launch.id,
                application_id=hashlib.sha256(
                    str(self.store.engine.url.set(password=None)).encode()
                ).hexdigest()[:16],
                resource_id=resource_id,
                container_name=f'oh-managed-{resource_id}',
                initializer_name=f'oh-managed-{resource_id}-init',
                owned_volume_names=[f'oh-managed-{resource_id}-workspace'],
                launch_spec_ciphertext=self.store.encrypt_launch(launch, cors_origins),
                session_key_ciphertext=self.store.jwt_service.encrypt_value(
                    session_key
                ),
                workspace_key_ciphertext=self.store.jwt_service.encrypt_value(
                    workspace_key
                ),
                desired_state='starting',
                session_api_key_hash=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            try:
                await self.store.insert(row)
            except IntegrityError:
                raise SandboxError(
                    'Sandbox ID is already reserved', status_code=409
                ) from None
            try:
                await self._admit(owner, excluding=sandbox_id)
                await self._allocate(row)
                container = await self._container(row)
                if container is None:
                    raise SandboxError('Allocated sandbox container is missing')
                await self._initialize(row, container)
                return self._info(row, SandboxStatus.RUNNING, container)
            except Exception:
                await self._rollback_allocation(row)
                raise SandboxError(
                    'Sandbox allocation or initialization failed. Please retry.',
                    status_code=503,
                ) from None
            except BaseException:
                await self._rollback_allocation(row)
                raise

    async def _rollback_allocation(self, row: StoredManagedSandbox) -> None:
        # Reservation is durable even if cleanup/DB is temporarily down.
        row.session_api_key_hash = None
        row.desired_state = 'cleanup'
        try:
            await self.store.save(row)
            await self._cleanup(row)
            await self.store.remove(row)
        except Exception:
            _logger.warning('Managed sandbox cleanup needs retry: %s', row.id)

    async def _allocate(self, row: StoredManagedSandbox) -> None:
        client = await self._client()
        launch, cors = self.store.launch(row)
        volume_name = row.owned_volume_names[0]
        # Docker volume create is idempotent and returns an existing volume, so
        # verify both before and after create. Never adopt an unlabeled name.
        try:
            volume = await self._docker(client.volumes.get, volume_name)
            self._check_labels(volume, row, 'workspace')
        except NotFound:
            volume = await self._docker(
                client.volumes.create,
                name=volume_name,
                labels=self._labels(row, 'workspace'),
            )
            self._check_labels(volume, row, 'workspace')
        await self._prepare_volume(row, launch)
        env = {
            name: value.get_secret_value() for name, value in launch.initial_env.items()
        }
        # Agent Server constructs its CORS middleware before /api/init. Send
        # the pinned origins at native startup as well as in the init payload.
        env = {
            name: value
            for name, value in env.items()
            if not name.startswith('OH_ALLOW_CORS_ORIGINS_')
        }
        env['OH_ALLOW_CORS_ORIGINS'] = json.dumps(cors)
        for index, origin in enumerate(cors):
            env[f'OH_ALLOW_CORS_ORIGINS_{index}'] = origin
        for name, port in launch.docker.ports.items():
            env[name] = str(port)
        if VSCODE in launch.docker.ports:
            env['OH_VSCODE_PORT'] = str(launch.docker.ports['VSCODE'])
        mounts = [
            docker.types.Mount(
                launch.docker.workspace_mount_path, volume_name, type='volume'
            )
        ]
        mounts.extend(
            docker.types.Mount(
                mount.target, mount.source, type='bind', read_only=mount.read_only
            )
            for mount in launch.docker.mounts
        )
        container = await self._docker(
            client.containers.create,
            image=launch.image,
            command=launch.command,
            name=row.container_name,
            environment=env,
            working_dir=launch.working_dir,
            mounts=mounts,
            labels=self._labels(row),
            detach=True,
            ports={
                f'{port}/tcp': (launch.docker.bind_host, 0)
                for port in launch.docker.ports.values()
            },
            network=launch.docker.network,
            extra_hosts=launch.docker.extra_hosts,
            user=launch.docker.user,
            mem_limit=launch.docker.mem_limit,
            nano_cpus=launch.docker.nano_cpus,
            auto_remove=False,
        )
        self._check_labels(container, row, 'sandbox')
        if not container.id:
            raise SandboxError('Docker returned a container without an ID')
        row.container_id = container.id
        await self.store.save(row)
        await self._docker(container.start)

    async def _prepare_volume(
        self, row: StoredManagedSandbox, launch: DockerLaunchSpec
    ) -> None:
        client = await self._client()
        # Pull through Docker's configured registry credentials if not cached.
        try:
            image = await self._docker(client.images.get, launch.image)
        except NotFound:
            image = await self._docker(client.images.pull, launch.image)
        user = (
            launch.docker.user
            or DockerResourceAttributes.model_validate(image.attrs).config.user
            or '0'
        )
        target = launch.docker.workspace_mount_path
        paths = [launch.working_dir] + [
            launch.initial_env[name].get_secret_value()
            for name in (
                'OH_CONVERSATIONS_PATH',
                'OH_BASH_EVENTS_DIR',
                'OH_CONVERSATION_WORKTREE_ROOT',
                'OH_PERSISTENCE_DIR',
            )
        ]
        # Recovery after a worker crash: only remove a matching helper.
        await self._remove_named_container(row, row.initializer_name, 'initializer')
        helper = await self._docker(
            client.containers.create,
            image=launch.image,
            entrypoint=['/bin/sh', '-c'],
            command=[_VOLUME_SETUP, 'volume-setup', user, target, *paths],
            user='0',
            name=row.initializer_name,
            labels=self._labels(row, 'initializer'),
            mounts=[
                docker.types.Mount(target, row.owned_volume_names[0], type='volume')
            ],
            network_disabled=True,
            detach=True,
            auto_remove=False,
        )
        self._check_labels(helper, row, 'initializer')
        await self._docker(helper.start)
        result = await self._docker(helper.wait, timeout=30)
        if DockerWaitResult.model_validate(result).status_code != 0:
            raise SandboxError('Could not prepare sandbox workspace permissions')
        await self._docker(helper.remove)

    async def _initialize(
        self, row: StoredManagedSandbox, container: Container
    ) -> None:
        launch, cors = self.store.launch(row)
        await self._docker(container.reload)
        generation = self._generation(container)
        if not generation or self._state(container) != 'running':
            raise SandboxError('Sandbox container is not running')
        url = self._agent_url(row, container)
        deadline = time.monotonic() + self.start_sandbox_timeout
        while time.monotonic() < deadline:
            try:
                state = await self._init_state(url)
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(self.poll_interval)
                continue
            if state == 'ready':
                if generation not in (
                    row.initialized_generation,
                    row.initializing_generation,
                ):
                    raise SandboxError('Sandbox was initialized by an unknown process')
                if await self._ready(row, container):
                    await self._docker(container.reload)
                    if self._generation(container) != generation:
                        raise SandboxError('Sandbox restarted during initialization')
                    # A crash after successful POST but before DB activation is
                    # recoverable with the already persisted pending generation.
                    row.initialized_generation = generation
                    row.initializing_generation = None
                    row.desired_state = 'running'
                    row.session_api_key_hash = hash_session_key(
                        self.store.credentials(row).session_key.get_secret_value()
                    )
                    await self.store.save(row)
                    return
            elif state == 'dormant':
                if row.initializing_generation != generation:
                    # Native restart loses memory. Persist fresh auth before the
                    # POST, while preserving the workspace encryption key.
                    row.session_key_ciphertext = self.store.jwt_service.encrypt_value(
                        secrets.token_urlsafe(32)
                    )
                    row.initializing_generation = generation
                    row.session_api_key_hash = None
                    row.desired_state = 'starting'
                    await self.store.save(row)
                credentials = self.store.credentials(row)
                body: _InitPayload = {
                    'session_api_keys': [credentials.session_key.get_secret_value()],
                    'secret_key': credentials.workspace_key.get_secret_value(),
                    'conversations_path': launch.initial_env[
                        'OH_CONVERSATIONS_PATH'
                    ].get_secret_value(),
                    'bash_events_dir': launch.initial_env[
                        'OH_BASH_EVENTS_DIR'
                    ].get_secret_value(),
                    'conversation_worktree_root': launch.initial_env[
                        'OH_CONVERSATION_WORKTREE_ROOT'
                    ].get_secret_value(),
                    'allow_cors_origins': cors,
                    'web_url': next(
                        item.url
                        for item in self._urls(row, container)
                        if item.name == AGENT_SERVER
                    ),
                }
                if launch.docker.webhook_url:
                    body['webhooks'] = [{'base_url': launch.docker.webhook_url}]
                # Validate against the SDK, but send the explicit wire payload:
                # serializing InitRequest would mask its SecretStr workspace key.
                InitRequest.model_validate(body)
                response = await self.httpx_client.post(
                    f'{url}/api/init',
                    headers={
                        'X-Init-API-Key': credentials.workspace_key.get_secret_value()
                    },
                    json=body,
                    timeout=max(1, deadline - time.monotonic()),
                )
                response.raise_for_status()
            await asyncio.sleep(self.poll_interval)
        raise SandboxError('Sandbox initialization timed out')

    async def _resume(self, row: StoredManagedSandbox) -> bool:
        if row.desired_state in ('deleting', 'cleanup'):
            raise SandboxError(
                'Sandbox cleanup must complete before resuming', status_code=409
            )
        # Both resume_sandbox and start_sandbox(existing_id) resolve an owned
        # inventory row before reaching here. Validate retained conversations
        # before any Docker transition or credential rotation.
        await self.validate_resume_configuration(row.id)
        container = await self._container(row)
        if container is None:
            return False
        state = self._state(container)
        if (
            state == 'running'
            and row.desired_state == 'running'
            and row.session_api_key_hash
            and row.initialized_generation == self._generation(container)
        ):
            # Idempotent resume does not rotate keys or disturb another process.
            if await self._ready(row, container):
                return True
        await self._admit(row.created_by_user_id, excluding=row.id)
        row.session_api_key_hash = None
        row.desired_state = 'starting'
        await self.store.save(row)
        try:
            if state == 'paused':
                await self._docker(container.unpause)
            elif state in ('exited', 'created'):
                await self._docker(container.start)
            elif state != 'running':
                raise SandboxError('Docker container cannot currently be resumed')
            await self._initialize(row, container)
            return True
        except Exception:
            row.session_api_key_hash = None
            row.desired_state = 'error'
            await self.store.save(row)
            raise SandboxError(
                'Sandbox resume failed. Please retry.', status_code=503
            ) from None

    async def resume_sandbox(self, sandbox_id: str) -> bool:
        row = await self.store.get(sandbox_id)
        if row is None:
            return False
        async with (
            self.store.lock(f'owner:{row.created_by_user_id}'),
            self.store.lock(f'sandbox:{sandbox_id}'),
        ):
            row = await self.store.get(sandbox_id)
            return await self._resume(row) if row else False

    async def _pause(self, row: StoredManagedSandbox) -> bool:
        if row.desired_state in ('deleting', 'cleanup'):
            raise SandboxError(
                'Sandbox cleanup must complete before pausing', status_code=409
            )
        # Revoke before the native transition. On failure, retain a cap-counted
        # state so retries cannot silently admit another running sandbox.
        row.session_api_key_hash = None
        row.desired_state = 'starting'
        await self.store.save(row)
        container = await self._container(row)
        if container is None:
            row.session_api_key_hash = None
            row.desired_state = 'paused'
            await self.store.save(row)
            return True
        state = self._state(container)
        if state == 'running':
            await self._docker(container.pause)
        elif state not in ('paused', 'exited', 'created'):
            raise SandboxError(
                'Docker container cannot currently be paused', status_code=503
            )
        row.session_api_key_hash = None
        row.desired_state = 'paused'
        await self.store.save(row)
        return True

    async def pause_sandbox(self, sandbox_id: str) -> bool:
        if await self.store.get(sandbox_id) is None:
            return False
        async with self.store.lock(f'sandbox:{sandbox_id}'):
            row = await self.store.get(sandbox_id)
            return await self._pause(row) if row else False

    async def pause_old_sandboxes(self, max_num_sandboxes: int) -> list[str]:
        if max_num_sandboxes < 0:
            raise ValueError('max_num_sandboxes must not be negative')
        owner = await self.user_context.get_user_id()
        if not owner:
            return []
        async with self.store.lock(f'owner:{owner}'):
            return await self._admit(owner, retain=max_num_sandboxes)

    async def _remove_named_container(
        self, row: StoredManagedSandbox, name: str, role: str
    ) -> None:
        client = await self._client()
        try:
            container = await self._docker(
                client.containers.get,
                row.container_id if role == 'sandbox' and row.container_id else name,
            )
        except NotFound:
            return
        self._check_labels(container, row, role)
        if role == 'sandbox' and row.container_id and row.container_id != container.id:
            raise ForeignDockerResource()
        try:
            await self._docker(container.remove, force=True)
        except NotFound:
            pass

    async def _cleanup(self, row: StoredManagedSandbox) -> None:
        await self._remove_named_container(row, row.container_name, 'sandbox')
        await self._remove_named_container(row, row.initializer_name, 'initializer')
        client = await self._client()
        for name in row.owned_volume_names:
            try:
                volume = await self._docker(client.volumes.get, name)
                self._check_labels(volume, row, 'workspace')
                await self._docker(volume.remove)
            except NotFound:
                pass

    async def delete_sandbox(self, sandbox_id: str) -> bool:
        if await self.store.get(sandbox_id) is None:
            return False
        async with self.store.lock(f'sandbox:{sandbox_id}'):
            row = await self.store.get(sandbox_id)
            if row is None:
                return False
            row.desired_state = 'deleting'
            row.session_api_key_hash = None
            await self.store.save(row)
            try:
                await self._cleanup(row)
                await self.store.remove(row)
            except Exception:
                raise SandboxDeleteRetryError(
                    'Sandbox cleanup is incomplete. Please retry.'
                ) from None
            return True

    async def archive_conversation_workspace(
        self,
        sandbox_id: str,
        conversation_id: str | None = None,
        workspace_path: str | None = None,
    ) -> bool:
        if not workspace_archive.archive_enabled():
            return True
        try:
            row = await self.store.get(sandbox_id)
            if row is None:
                return True
            info = await self.get_sandbox(sandbox_id)
            if not info or info.status != SandboxStatus.RUNNING:
                # Missing containers can still have durable data in an owned
                # volume; REQUIRED capture must not silently discard that data.
                return not workspace_archive.archive_required()
            launch, _ = self.store.launch(row)
            if workspace_path is None:
                grouping = (
                    await self.user_context.get_user_info()
                ).sandbox_grouping_strategy
                workspace_path = grouped_workspace_dir(
                    launch.working_dir, grouping, conversation_id or sandbox_id
                )
            path = PurePosixPath(workspace_path)
            root = PurePosixPath(launch.docker.workspace_mount_path)
            if '..' in path.parts or (path != root and root not in path.parents):
                raise SandboxError('Archive path is outside the managed workspace')
            return await workspace_archive.archive_workspace(
                self.httpx_client,
                {
                    'url': self._get_agent_server_url(info),
                    'session_api_key': info.session_api_key,
                },
                sandbox_id,
                archive_path=workspace_path,
                conversation_id=conversation_id,
            )
        except Exception:
            return not workspace_archive.archive_required()


class ManagedDockerSandboxServiceInjector(SandboxServiceInjector):
    _provider_config: SandboxProviderConfig = PrivateAttr()

    def __init__(
        self,
        *,
        provider_config: SandboxProviderConfig,
        **kwargs: Unpack[_InjectorOptions],
    ) -> None:
        BaseModel.__init__(self, **kwargs)
        self._provider_config = provider_config

    @property
    def provider_config(self) -> SandboxProviderConfig:
        return self._provider_config

    max_num_sandboxes: int = Field(
        default_factory=lambda: int(os.getenv('OH_SANDBOX_MAX_NUM_SANDBOXES', '5')),
        ge=1,
    )
    start_sandbox_timeout: int = Field(default=120, ge=1)

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxService, None]:
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
            get_sandbox_spec_service(state, request) as catalog,
            get_httpx_client(state, request) as httpx_client,
            get_jwt_service(state, request) as jwt_service,
        ):
            if not isinstance(catalog, ConfiguredSandboxSpecService):
                raise SandboxError(
                    'Managed Docker requires the configured template catalog'
                )
            engine = await config.db_session.get_async_db_engine()
            if engine.dialect.name != 'postgresql':
                raise SandboxError('Managed Docker requires PostgreSQL')
            store = SQLSandboxStore(
                engine,
                jwt_service,
                await user_context.get_user_id(),
                user_context is ADMIN,
            )
            service = ManagedDockerSandboxService(
                sandbox_spec_service=catalog,
                store=store,
                user_context=user_context,
                httpx_client=httpx_client,
                default_sandbox_spec_id=self.provider_config.default_template or '',
                max_num_sandboxes=self.max_num_sandboxes,
                start_sandbox_timeout=self.start_sandbox_timeout,
                web_url=config.web_url,
                permitted_cors_origins=config.permitted_cors_origins,
            )
            try:
                yield service
            finally:
                if service.docker_client is not None:
                    await service._docker(service.docker_client.close)
