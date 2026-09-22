import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import AsyncGenerator

import base62
import docker
import httpx
from docker.errors import APIError, NotFound
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.utils import utc_now
from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.docker_sandbox_spec_service import get_docker_client
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
    SESSION_API_KEY_VARIABLE,
    WEBHOOK_CALLBACK_VARIABLE,
    SandboxService,
    SandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    resolve_sandbox_spec,
)
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    StoredSandbox,
    get_stored_sandbox,
    get_stored_sandbox_by_session_api_key,
    hash_session_api_key,
    search_stored_sandboxes,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)

_logger = logging.getLogger(__name__)
STARTUP_GRACE_SECONDS = 15

# Sandbox identity lives in the `v1_sandbox` table (see `sandbox_store`).
# These labels are the reconciliation tag: they are what a later reaper needs
# to find a container the app has no row for.
MANAGED_LABEL = 'openhands.managed'
SANDBOX_SPEC_ID_LABEL = 'openhands.sandbox_spec_id'
CREATED_BY_USER_ID_LABEL = 'openhands.created_by_user_id'


def _get_use_host_network_default() -> bool:
    """Get the default value for use_host_network from environment variables.

    This function is called at runtime (not at class definition time) to ensure
    that environment variable changes are picked up correctly.
    """
    value = os.getenv('AGENT_SERVER_USE_HOST_NETWORK', '')
    return value.lower() in ('true', '1', 'yes')


def _get_kvm_enabled_default() -> bool:
    """Get the default value for kvm_enabled from environment variables."""
    value = os.getenv('SANDBOX_KVM_ENABLED', '')
    return value.lower() in ('true', '1', 'yes')


class VolumeMount(BaseModel):
    """Mounted volume within the container."""

    host_path: str
    container_path: str
    mode: str = 'rw'

    model_config = ConfigDict(frozen=True)


class ExposedPort(BaseModel):
    """Exposed port within container to be matched to a free port on the host."""

    name: str
    description: str
    container_port: int = 8000

    model_config = ConfigDict(frozen=True)


@dataclass
class DockerSandboxService(SandboxService):
    """Sandbox service built on docker.

    The Docker API does not currently support async operations, so some of these operations will block.
    Given that the docker API is intended for local use on a single machine, this is probably acceptable.
    """

    sandbox_spec_service: SandboxSpecService
    container_name_prefix: str
    host_port: int
    container_url_pattern: str
    mounts: list[VolumeMount]
    exposed_ports: list[ExposedPort]
    health_check_path: str | None
    httpx_client: httpx.AsyncClient
    max_num_sandboxes: int
    user_context: UserContext
    db_session: AsyncSession
    web_url: str | None = None
    permitted_cors_origins: list[str] = field(default_factory=list)
    extra_hosts: dict[str, str] = field(default_factory=dict)
    docker_client: docker.DockerClient = field(default_factory=get_docker_client)
    startup_grace_seconds: int = STARTUP_GRACE_SECONDS
    use_host_network: bool = False
    kvm_enabled: bool = False

    async def _get_stored_sandbox(self, sandbox_id: str) -> StoredSandbox | None:
        """Get a sandbox row, or None when the caller may not see it."""
        return await get_stored_sandbox(
            self.db_session, self.user_context, DOCKER_BACKEND, sandbox_id
        )

    def _managed_containers_by_name(self) -> dict[str, object]:
        """Every managed container on the host, indexed by name.

        One daemon call for a whole page. Ownership has already been decided
        by the query against `v1_sandbox`, so this only supplies live status.
        """
        containers = self.docker_client.containers.list(
            all=True, filters={'label': f'{MANAGED_LABEL}=true'}
        )
        # The name is the sandbox id, so an unnamed container cannot be
        # matched to a row and is left for a reconciler to deal with.
        return {container.name: container for container in containers if container.name}

    def _get_container(self, sandbox_id: str):
        """Get a container by name, or None when the daemon has no such one.

        ``NotFound`` is an answer - the container is gone, and the row it
        belongs to reports MISSING. A daemon that failed to answer is not, so
        it is raised rather than turned into MISSING, which would archive
        every live conversation for the length of the outage.
        """
        try:
            return self.docker_client.containers.get(sandbox_id)
        except NotFound:
            return None
        except APIError as exc:
            raise SandboxError(f'Could not read container {sandbox_id}: {exc}') from exc

    def _find_unused_port(self) -> int:
        """Find an unused port on the host machine."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _docker_status_to_sandbox_status(self, docker_status: str) -> SandboxStatus:
        """Convert Docker container status to SandboxStatus."""
        status_mapping = {
            'running': SandboxStatus.RUNNING,
            'paused': SandboxStatus.PAUSED,
            # The stop button was pressed in the docker console
            'exited': SandboxStatus.PAUSED,
            'created': SandboxStatus.STARTING,
            'restarting': SandboxStatus.STARTING,
            'removing': SandboxStatus.MISSING,
            'dead': SandboxStatus.ERROR,
        }
        return status_mapping.get(docker_status.lower(), SandboxStatus.ERROR)

    def _get_container_env_vars(self, container) -> dict[str, str | None]:
        env_vars_list = container.attrs['Config']['Env']
        result = {}
        for env_var in env_vars_list:
            if '=' in env_var:
                key, value = env_var.split('=', 1)
                result[key] = value
            else:
                # Handle cases where an environment variable might not have a value
                result[env_var] = None
        return result

    async def _to_sandbox_info(
        self, stored_sandbox: StoredSandbox, container
    ) -> SandboxInfo:
        """Build a SandboxInfo from the stored row plus its live container.

        Identity comes from the row. The container supplies status, the
        session API key (only its hash is stored) and the port mappings. A row
        with no container is MISSING — the container was removed outside the
        app, and the conversation is archived.
        """
        if container is None:
            return SandboxInfo(
                id=stored_sandbox.id,
                created_by_user_id=stored_sandbox.created_by_user_id,
                sandbox_spec_id=stored_sandbox.sandbox_spec_id,
                status=SandboxStatus.MISSING,
                session_api_key=None,
                exposed_urls=None,
                created_at=stored_sandbox.created_at,
            )

        status = self._docker_status_to_sandbox_status(container.status)

        # Get URL and session key for running containers
        exposed_urls = None
        session_api_key = None

        if status == SandboxStatus.RUNNING:
            # Get session API key first
            env = self._get_container_env_vars(container)
            session_api_key = env.get(SESSION_API_KEY_VARIABLE)

            # Get the exposed port mappings
            exposed_urls = []

            # Check if container is using host network mode
            network_mode = container.attrs.get('HostConfig', {}).get('NetworkMode', '')
            is_host_network = network_mode == 'host'

            if is_host_network:
                # Host network mode: container ports are directly accessible on host
                for exposed_port in self.exposed_ports:
                    host_port = exposed_port.container_port
                    url = self.container_url_pattern.format(port=host_port)

                    # VSCode URLs require the api_key and working dir
                    if exposed_port.name == VSCODE:
                        url += f'/?tkn={session_api_key}&folder={container.attrs["Config"]["WorkingDir"]}'

                    exposed_urls.append(
                        ExposedUrl(
                            name=exposed_port.name,
                            url=url,
                            port=exposed_port.container_port,
                        )
                    )
            else:
                # Bridge network mode: use port bindings
                port_bindings = container.attrs.get('NetworkSettings', {}).get(
                    'Ports', {}
                )
                if port_bindings:
                    for container_port, host_bindings in port_bindings.items():
                        if host_bindings:
                            host_port = int(host_bindings[0]['HostPort'])
                            matching_port = next(
                                (
                                    ep
                                    for ep in self.exposed_ports
                                    if container_port == f'{ep.container_port}/tcp'
                                ),
                                None,
                            )
                            if matching_port:
                                url = self.container_url_pattern.format(port=host_port)

                                # VSCode URLs require the api_key and working dir
                                if matching_port.name == VSCODE:
                                    url += f'/?tkn={session_api_key}&folder={container.attrs["Config"]["WorkingDir"]}'

                                exposed_urls.append(
                                    ExposedUrl(
                                        name=matching_port.name,
                                        url=url,
                                        port=matching_port.container_port,
                                    )
                                )

        return SandboxInfo(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
            sandbox_spec_id=stored_sandbox.sandbox_spec_id,
            status=status,
            session_api_key=session_api_key,
            exposed_urls=exposed_urls,
            created_at=stored_sandbox.created_at,
        )

    async def _to_checked_sandbox_info(
        self, stored_sandbox: StoredSandbox, container
    ) -> SandboxInfo:
        sandbox_info = await self._to_sandbox_info(stored_sandbox, container)
        if self.health_check_path is not None and sandbox_info.exposed_urls:
            app_server_url = next(
                exposed_url.url
                for exposed_url in sandbox_info.exposed_urls
                if exposed_url.name == AGENT_SERVER
            )
            try:
                # When running in Docker, replace localhost hostname with host.docker.internal for internal requests
                app_server_url = replace_localhost_hostname_for_docker(app_server_url)

                response = await self.httpx_client.get(
                    f'{app_server_url}{self.health_check_path}'
                )
                response.raise_for_status()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Get the started_at from the docker container info and fallback to sandbox created_at
                try:
                    state = container.attrs['State']
                    started_at = datetime.fromisoformat(state['StartedAt'])
                except Exception:
                    _logger.debug('Error getting container start time')
                    started_at = sandbox_info.created_at

                # If the server has exceeded the startup grace period, it's an error
                if started_at < utc_now() - timedelta(
                    seconds=self.startup_grace_seconds
                ):
                    _logger.info(
                        f'Sandbox server not running: {app_server_url} : {exc}'
                    )
                    sandbox_info.status = SandboxStatus.ERROR
                else:
                    _logger.debug(
                        f'Sandbox server not yet available (still starting): '
                        f'{app_server_url} : {exc}'
                    )
                    sandbox_info.status = SandboxStatus.STARTING
                sandbox_info.exposed_urls = None
                sandbox_info.session_api_key = None
        return sandbox_info

    async def search_sandboxes(
        self,
        page_id: str | None = None,
        limit: int = 100,
    ) -> SandboxPage:
        """Search for sandboxes.

        One query against `v1_sandbox` for the page, then one daemon call for
        the live status of everything on it.
        """
        page = await search_stored_sandboxes(
            self.db_session, self.user_context, DOCKER_BACKEND, page_id, limit
        )
        try:
            containers_by_name = self._managed_containers_by_name()
        except APIError as exc:
            # Not an empty page and not a page of MISSING: the first hides the
            # sandbox limit from `pause_old_sandboxes`, the second archives
            # every live conversation for the length of the outage.
            raise SandboxError(f'Could not list containers: {exc}') from exc

        items = [
            await self._to_checked_sandbox_info(
                stored_sandbox, containers_by_name.get(stored_sandbox.id)
            )
            for stored_sandbox in page.items
        ]
        return SandboxPage(items=items, next_page_id=page.next_page_id)

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        """Get a single sandbox info."""
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return None
        return await self._to_checked_sandbox_info(
            stored_sandbox, self._get_container(sandbox_id)
        )

    async def _get_stored_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> StoredSandbox | None:
        """Find the caller's sandbox holding the given session API key."""
        return await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, DOCKER_BACKEND, session_api_key
        )

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        """Get a single sandbox by session API key."""
        stored_sandbox = await self._get_stored_sandbox_by_session_api_key(
            session_api_key
        )
        if stored_sandbox is None:
            return None
        return await self._to_checked_sandbox_info(
            stored_sandbox, self._get_container(stored_sandbox.id)
        )

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        """Get persisted sandbox identity by session API key.

        An indexed lookup on the key hash, with no daemon call. This runs on
        the webhook path, which used to scan every container's environment.
        """
        stored_sandbox = await self._get_stored_sandbox_by_session_api_key(
            session_api_key
        )
        if stored_sandbox is None:
            return None
        return SandboxRecord(
            id=stored_sandbox.id,
            created_by_user_id=stored_sandbox.created_by_user_id,
        )

    async def start_sandbox(
        self, sandbox_spec_id: str | None = None, sandbox_id: str | None = None
    ) -> SandboxInfo:
        """Start a new sandbox."""
        # Warn about port collision risk when using host network mode with multiple sandboxes
        if self.use_host_network and self.max_num_sandboxes > 1:
            _logger.warning(
                'Host network mode is enabled with max_num_sandboxes > 1. '
                'Multiple sandboxes will attempt to bind to the same ports, '
                'which may cause port collision errors. Consider setting '
                'max_num_sandboxes=1 when using host network mode.'
            )

        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        user_default_spec_id = await self.user_context.get_default_sandbox_spec_id()
        sandbox_spec = await resolve_sandbox_spec(
            sandbox_spec_id,
            user_default_spec_id,
            self.sandbox_spec_service,
            _logger,
        )

        # Generate a sandbox id if none was provided
        if sandbox_id is None:
            sandbox_id = base62.encodebytes(os.urandom(16))

        # Generate container name and session api key
        container_name = f'{self.container_name_prefix}{sandbox_id}'
        session_api_key = base62.encodebytes(os.urandom(32))

        # Prepare environment variables
        env_vars = sandbox_spec.initial_env.copy()
        env_vars[SESSION_API_KEY_VARIABLE] = session_api_key
        env_vars[WEBHOOK_CALLBACK_VARIABLE] = (
            f'http://host.docker.internal:{self.host_port}/api/v1/webhooks'
        )

        # Set CORS origins for remote browser access when web_url is configured.
        # This allows the agent-server container to accept requests from the
        # frontend when running OpenHands on a remote machine.
        # Each origin gets its own indexed env var (OH_ALLOW_CORS_ORIGINS_0, _1, etc.)
        cors_origins: list[str] = []
        if self.web_url:
            cors_origins.append(self.web_url)
        cors_origins.extend(self.permitted_cors_origins)
        # Deduplicate while preserving order
        seen: set[str] = set()
        for origin in cors_origins:
            if origin not in seen:
                seen.add(origin)
                idx = len(seen) - 1
                env_vars[f'OH_ALLOW_CORS_ORIGINS_{idx}'] = origin

        # Prepare port mappings and add port environment variables
        # When using host network, container ports are directly accessible on the host
        # so we use the container ports directly instead of mapping to random host ports
        port_mappings: dict[int, int] | None = None
        if self.use_host_network:
            # Host network mode: container ports are directly accessible
            for exposed_port in self.exposed_ports:
                env_vars[exposed_port.name] = str(exposed_port.container_port)
        else:
            # Bridge network mode: map container ports to random host ports
            port_mappings = {}
            for exposed_port in self.exposed_ports:
                host_port = self._find_unused_port()
                port_mappings[exposed_port.container_port] = host_port
                env_vars[exposed_port.name] = str(exposed_port.container_port)

        # Labels are the reconciliation tag, not the record: they are how a
        # later reaper finds a container whose row was rolled back.
        labels = {
            MANAGED_LABEL: 'true',
            SANDBOX_SPEC_ID_LABEL: sandbox_spec.id,
        }
        user_id = await self.user_context.get_user_id()
        if user_id:
            labels[CREATED_BY_USER_ID_LABEL] = user_id

        # The id is ours, so the record goes in before the container exists.
        # The flush keeps that ordering real inside the transaction, which the
        # request commits on its way out.
        stored_sandbox = StoredSandbox(
            id=container_name,
            backend=DOCKER_BACKEND,
            created_by_user_id=user_id,
            sandbox_spec_id=sandbox_spec.id,
            session_api_key_hash=hash_session_api_key(session_api_key),
            created_at=utc_now(),
        )
        self.db_session.add(stored_sandbox)
        await self.db_session.flush()

        # Prepare volumes
        volumes = {
            mount.host_path: {
                'bind': mount.container_path,
                'mode': mount.mode,
            }
            for mount in self.mounts
        }

        # Determine network mode
        network_mode = 'host' if self.use_host_network else None

        if self.use_host_network:
            _logger.info(f'Starting sandbox {container_name} with host network mode')

        # Determine devices to pass through (e.g., /dev/kvm for hardware virtualization)
        devices = ['/dev/kvm:/dev/kvm:rwm'] if self.kvm_enabled else None

        if self.kvm_enabled:
            _logger.info(
                f'Starting sandbox {container_name} with KVM device passthrough'
            )

        try:
            # Create and start the container
            container = self.docker_client.containers.run(  # type: ignore[call-overload,misc]
                image=sandbox_spec.id,
                command=sandbox_spec.command,  # Use default command from image
                remove=False,
                name=container_name,
                environment=env_vars,
                ports=port_mappings,
                volumes=volumes,
                working_dir=sandbox_spec.working_dir,
                labels=labels,
                detach=True,
                # Use Docker's tini init process to ensure proper signal handling and reaping of
                # zombie child processes.
                init=True,
                # Allow agent-server containers to resolve host.docker.internal
                # and other custom hostnames for LAN deployments
                # Note: extra_hosts is not needed with host network mode
                extra_hosts=self.extra_hosts
                if self.extra_hosts and not self.use_host_network
                else None,
                # Network mode: 'host' for host networking, None for default bridge
                network_mode=network_mode,
                # Device passthrough for KVM hardware virtualization
                devices=devices,
            )

            return await self._to_sandbox_info(stored_sandbox, container)

        except APIError as e:
            raise SandboxError('Failed to start container') from e

    async def resume_sandbox(self, sandbox_id: str) -> bool:
        """Resume a paused sandbox.

        The session API key is unchanged. Docker bakes it into the container
        environment at create, so rotating it means replacing the container
        and losing the workspace with it.
        """
        # Enforce sandbox limits by cleaning up old sandboxes
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)

        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        container = self._get_container(sandbox_id)
        if container is None:
            return False

        try:
            if container.status == 'paused':
                container.unpause()
            elif container.status == 'exited':
                container.start()
            return True
        except (NotFound, APIError):
            return False

    async def pause_sandbox(self, sandbox_id: str) -> bool:
        """Pause a running sandbox.

        The stored key hash is kept, for the same reason `resume_sandbox`
        cannot rotate it.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False
        container = self._get_container(sandbox_id)
        if container is None:
            return False

        try:
            if container.status == 'running':
                container.pause()
            return True
        except (NotFound, APIError):
            return False

    async def delete_sandbox(self, sandbox_id: str) -> bool:
        """Delete a sandbox.

        The row is soft deleted rather than removed, and its key hash is
        cleared so a leaked key stops resolving. A container the daemon has
        already lost still retires its row, so the record cannot outlive what
        it describes.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id)
        if stored_sandbox is None:
            return False

        container = self._get_container(sandbox_id)
        if container is not None:
            try:
                # Stop the container if it's running
                if container.status in ['running', 'paused']:
                    container.stop(timeout=10)

                # Remove the container
                container.remove()
            except NotFound:
                pass
            except APIError:
                _logger.exception(
                    f'Error deleting container {sandbox_id}', stack_info=True
                )
                return False

        stored_sandbox.deleted_at = utc_now()
        stored_sandbox.session_api_key_hash = None
        return True


class DockerSandboxServiceInjector(SandboxServiceInjector):
    """Dependency injector for docker sandbox services."""

    container_url_pattern: str = Field(
        default='http://localhost:{port}',
        description=(
            'URL pattern for exposed sandbox ports. Use {port} as placeholder. '
            'For remote access, set to your server IP (e.g., http://192.168.1.100:{port}). '
            'Configure via OH_SANDBOX_CONTAINER_URL_PATTERN environment variable.'
        ),
    )
    host_port: int = Field(
        default=3000,
        description=(
            'The port on which the main OpenHands app server is running. '
            'Used for webhook callbacks from agent-server containers. '
            'If running OpenHands on a non-default port, set this to match. '
            'Configure via OH_SANDBOX_HOST_PORT environment variable.'
        ),
    )
    container_name_prefix: str = 'oh-agent-server-'
    max_num_sandboxes: int = Field(
        default=5,
        description='Maximum number of sandboxes allowed to run simultaneously',
    )
    mounts: list[VolumeMount] = Field(default_factory=list)
    exposed_ports: list[ExposedPort] = Field(
        default_factory=lambda: [
            ExposedPort(
                name=AGENT_SERVER,
                description=(
                    'The port on which the agent server runs within the container'
                ),
                container_port=8000,
            ),
            ExposedPort(
                name=VSCODE,
                description=(
                    'The port on which the VSCode server runs within the container'
                ),
                container_port=8001,
            ),
            ExposedPort(
                name=WORKER_1,
                description=(
                    'The first port on which the agent should start application servers.'
                ),
                container_port=8011,
            ),
            ExposedPort(
                name=WORKER_2,
                description=(
                    'The second port on which the agent should start application servers.'
                ),
                container_port=8012,
            ),
        ]
    )
    health_check_path: str | None = Field(
        default='/health',
        description=(
            'The url path in the sandbox agent server to check to '
            'determine whether the server is running'
        ),
    )
    extra_hosts: dict[str, str] = Field(
        default_factory=lambda: {'host.docker.internal': 'host-gateway'},
        description=(
            'Extra hostname mappings to add to agent-server containers. '
            'This allows containers to resolve hostnames like host.docker.internal '
            'for LAN deployments and MCP connections. '
            'Format: {"hostname": "ip_or_gateway"}'
        ),
    )
    startup_grace_seconds: int = Field(
        default=STARTUP_GRACE_SECONDS,
        description=(
            'Number of seconds were no response from the agent server is acceptable'
            'before it is considered an error'
        ),
    )
    use_host_network: bool = Field(
        default_factory=_get_use_host_network_default,
        description=(
            'Whether to use host networking mode for agent-server containers. '
            'When enabled, containers share the host network namespace, '
            'making all container ports directly accessible on the host. '
            'This is useful for reverse proxy setups where dynamic port mapping '
            'is problematic. Configure via AGENT_SERVER_USE_HOST_NETWORK environment variable.'
        ),
    )
    kvm_enabled: bool = Field(
        default_factory=_get_kvm_enabled_default,
        description=(
            'Whether to pass through /dev/kvm to sandbox containers for hardware '
            'virtualization support. When enabled, sandboxes can run KVM-accelerated '
            'virtual machines instead of using slower emulation. Requires the host '
            'to have KVM available (/dev/kvm must exist and be accessible). '
            'Configure via SANDBOX_KVM_ENABLED environment variable.'
        ),
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

        # Get web_url and permitted_cors_origins from global config
        config = get_global_config()
        web_url = config.web_url

        async with (
            get_user_context(state, request) as user_context,
            get_httpx_client(state) as httpx_client,
            get_sandbox_spec_service(state) as sandbox_spec_service,
            get_db_session(state, request) as db_session,
        ):
            yield DockerSandboxService(
                sandbox_spec_service=sandbox_spec_service,
                container_name_prefix=self.container_name_prefix,
                host_port=self.host_port,
                container_url_pattern=self.container_url_pattern,
                mounts=self.mounts,
                exposed_ports=self.exposed_ports,
                health_check_path=self.health_check_path,
                httpx_client=httpx_client,
                max_num_sandboxes=self.max_num_sandboxes,
                user_context=user_context,
                db_session=db_session,
                web_url=web_url,
                permitted_cors_origins=config.permitted_cors_origins,
                extra_hosts=self.extra_hosts,
                startup_grace_seconds=self.startup_grace_seconds,
                use_host_network=self.use_host_network,
                kvm_enabled=self.kvm_enabled,
            )
