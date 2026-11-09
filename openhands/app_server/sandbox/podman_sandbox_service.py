"""Podman sandbox service — a thin subclass of DockerSandboxService.

Podman exposes a Docker-compatible REST API (``podman system service``), so the
existing ``docker`` Python SDK talks to it when ``DOCKER_HOST`` points at the
Podman socket. This module only overrides what differs:

* ``backend`` — DB rows are tagged ``podman``.
* ``_host_gateway`` — sandboxes use ``host.containers.internal`` to reach the
  host (Podman rootless networking resolves it built-in).
* The client factory — connects to the Podman socket.
* ``extra_hosts`` default — uses Podman's convention.
"""

import os
from dataclasses import dataclass
from typing import AsyncGenerator, ClassVar

from fastapi import Request
from pydantic import ConfigDict, Field, model_validator

from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxService,
    ExposedPort,
    VolumeMount,
    _get_kvm_enabled_default,
    _get_use_host_network_default,
)
from openhands.app_server.sandbox.managed_sandbox_service import (
    ManagedSandboxServiceInjector,
)
from openhands.app_server.sandbox.podman_sandbox_spec_service import get_podman_client
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    WORKER_1,
    WORKER_2,
)
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.sandbox.sandbox_store import PODMAN_BACKEND
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.utils.podman_utils import (
    PODMAN_HOST_GATEWAY,
    replace_localhost_hostname_for_podman,
)

# Re-export VolumeMount and ExposedPort so config.py can import them from one place.
__all__ = [
    'PodmanSandboxService',
    'PodmanSandboxServiceInjector',
    'VolumeMount',
    'ExposedPort',
]


def _get_podman_network_default() -> str | None:
    """Get the default Podman network for sandbox containers from the environment."""
    return os.getenv('SANDBOX_PODMAN_NETWORK') or None


@dataclass
class PodmanSandboxService(DockerSandboxService):
    """Sandbox service built on Podman via its Docker-compatible API.

    Reuses every method of ``DockerSandboxService`` (container lifecycle,
    port finding, health checking, status mapping) because Podman's compat
    API returns the same container status strings and supports the same
    ``containers.run/get/stop/start/unpause/remove`` calls.
    """

    backend: ClassVar[str] = PODMAN_BACKEND

    def _replace_localhost_hostname(self, url: str) -> str:
        """Replace localhost with ``host.containers.internal`` for Podman."""
        return replace_localhost_hostname_for_podman(url)

    @property
    def _host_gateway(self) -> str:
        """The hostname sandboxes use to reach back to this server.

        Podman rootless networking resolves ``host.containers.internal``
        built-in, so no ``extra_hosts`` mapping is required in the common
        case. Rootful Podman may need the explicit ``host-gateway`` mapping
        supplied via ``extra_hosts``.
        """
        return PODMAN_HOST_GATEWAY


class PodmanSandboxServiceInjector(ManagedSandboxServiceInjector):
    """Dependency injector for Podman sandbox services.

    Mirrors ``DockerSandboxServiceInjector`` but swaps the host-gateway alias
    and the network description for Podman conventions.
    """

    backend: ClassVar[str] = PODMAN_BACKEND

    container_url_pattern: str = Field(
        default='http://localhost:{port}',
        description=(
            'URL pattern for exposed sandbox ports. Use {port} as placeholder. '
            'For remote access, set to your server IP (e.g., http://192.168.1.100:{port}). '
            '{sandbox_id} is the container name, for a proxy that routes to '
            'sandboxes on a Podman network. '
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
        default_factory=lambda: {PODMAN_HOST_GATEWAY: 'host-gateway'},
        description=(
            'Extra hostname mappings to add to agent-server containers. '
            'Podman rootless networking resolves host.containers.internal '
            'built-in, but rootful Podman may need this host-gateway mapping. '
            'Format: {"hostname": "ip_or_gateway"}'
        ),
    )
    startup_grace_seconds: int = Field(
        default=15,
        description=(
            'Number of seconds where no response from the agent server is acceptable '
            'before it is considered an error'
        ),
    )
    use_host_network: bool = Field(
        default_factory=_get_use_host_network_default,
        description=(
            'Whether to use host networking mode for agent-server containers. '
            'When enabled, containers share the host network namespace, '
            'making all container ports directly accessible on the host. '
            'Note: rootless Podman host networking uses slirp4netns, which '
            'does not fully share the host namespace. '
            'Configure via AGENT_SERVER_USE_HOST_NETWORK environment variable.'
        ),
    )
    network: str | None = Field(
        default_factory=_get_podman_network_default,
        description=(
            'Podman network to attach agent-server containers to instead of the '
            'default bridge. Containers on a user-defined network resolve each '
            'other by name, so sandboxes can reach Compose services by '
            'their service names. No ports are published: {port} in '
            'container_url_pattern is the container port, and the pattern must '
            'include {sandbox_id} for a proxy on the network to route by. Ignored '
            'with host networking. Configure via SANDBOX_PODMAN_NETWORK '
            'environment variable.'
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

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def _network_urls_name_the_sandbox(self) -> 'PodmanSandboxServiceInjector':
        if (
            self.network
            and not self.use_host_network
            and '{sandbox_id}' not in self.container_url_pattern
        ):
            raise ValueError(
                'SANDBOX_PODMAN_NETWORK needs a container_url_pattern that '
                'includes {sandbox_id}'
            )
        return self

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
        web_url = config.web_url

        async with (
            get_user_context(state, request) as user_context,
            get_httpx_client(state) as httpx_client,
            get_sandbox_spec_service(state) as sandbox_spec_service,
            get_db_session(state, request) as db_session,
        ):
            yield PodmanSandboxService(
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
                sandbox_web_url=config.sandbox_web_url,
                permitted_cors_origins=config.permitted_cors_origins,
                extra_hosts=self.extra_hosts,
                startup_grace_seconds=self.startup_grace_seconds,
                use_host_network=self.use_host_network,
                network=self.network,
                kvm_enabled=self.kvm_enabled,
                lifecycle=self.lifecycle,
                docker_client=get_podman_client(),
            )
