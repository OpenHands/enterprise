"""Podman sandbox spec service — image pulling via Podman's Docker-compatible API.

Mirrors ``DockerSandboxSpecServiceInjector`` but connects to the Podman socket
instead of the Docker daemon.
"""

import asyncio
import logging
import os
from typing import AsyncGenerator

import docker
from fastapi import Request
from pydantic import Field

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    SandboxSpecServiceInjector,
    get_agent_server_env,
    get_agent_server_image,
)
from openhands.app_server.services.injector import InjectorState

_global_podman_client: docker.DockerClient | None = None
_logger = logging.getLogger(__name__)


def _connect_to_podman() -> docker.DockerClient:
    """Connect to the Podman Docker-compatible API.

    Resolution order:
    1. ``DOCKER_HOST`` env var (explicit, user-controlled).
    2. ``PODMAN_SOCKET`` env var (convenience, Podman-specific).
    3. Rootless socket at ``$XDG_RUNTIME_DIR/podman/podman.sock``.
    4. Rootful socket at ``/run/podman/podman.sock``.
    5. Fall back to ``docker.from_env()`` (picks up ``DOCKER_HOST`` if set
       elsewhere, or the Docker socket if Podman isn't running).
    """
    failures = []

    # 1 & 2: explicit env var
    explicit = os.environ.get('DOCKER_HOST') or os.environ.get('PODMAN_SOCKET')
    if explicit:
        try:
            return docker.DockerClient(base_url=explicit)
        except docker.errors.DockerException as exc:
            failures.append(f'explicit socket {explicit}: {exc}')

    # 3: rootless socket
    xdg_runtime = os.environ.get('XDG_RUNTIME_DIR')
    if xdg_runtime:
        rootless_socket = f'unix://{xdg_runtime}/podman/podman.sock'
        try:
            client = docker.DockerClient(base_url=rootless_socket)
            client.ping()
            return client
        except Exception as exc:
            failures.append(f'rootless socket {rootless_socket}: {exc}')

    # 4: rootful socket
    rootful_socket = 'unix:///run/podman/podman.sock'
    try:
        client = docker.DockerClient(base_url=rootful_socket)
        client.ping()
        return client
    except Exception as exc:
        failures.append(f'rootful socket {rootful_socket}: {exc}')

    # 5: last resort
    try:
        return docker.from_env()
    except docker.errors.DockerException as exc:
        failures.append(f'docker.from_env(): {exc}')

    raise SandboxError(
        'Could not connect to the Podman daemon ('
        + '; '.join(failures)
        + '). Check that Podman is running and the compatibility API is '
        'started (e.g. `podman system service --time=0`), or set '
        'DOCKER_HOST to the socket path.'
    )


def get_podman_client() -> docker.DockerClient:
    global _global_podman_client
    if _global_podman_client is None:
        _global_podman_client = _connect_to_podman()
    return _global_podman_client


def get_default_podman_sandbox_specs():
    return [
        SandboxSpecInfo(
            id=get_agent_server_image(),
            command=['--port', '8000'],
            initial_env={
                'OPENVSCODE_SERVER_ROOT': '/openhands/.openvscode-server',
                'OH_ENABLE_VNC': '0',
                'LOG_JSON': 'true',
                'OH_CONVERSATIONS_PATH': '/workspace/conversations',
                'OH_BASH_EVENTS_DIR': '/workspace/bash_events',
                'PYTHONUNBUFFERED': '1',
                'ENV_LOG_LEVEL': '20',
                **get_agent_server_env(),
            },
            working_dir='/workspace/project',
        )
    ]


class PodmanSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    specs: list[SandboxSpecInfo] = Field(
        default_factory=get_default_podman_sandbox_specs,
        description='Preset list of sandbox specs',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        yield PresetSandboxSpecService(specs=self.specs)

    async def prepare(self) -> None:
        """Pull the spec images that are not on the Podman host."""
        await asyncio.gather(*[self.pull_spec_if_missing(spec) for spec in self.specs])

    async def pull_spec_if_missing(self, spec: SandboxSpecInfo):
        _logger.debug(f'Checking Podman Image: {spec.id}')
        try:
            podman_client = get_podman_client()
            try:
                podman_client.images.get(spec.id)
            except docker.errors.ImageNotFound:
                _logger.info(f'⬇️  Pulling Podman Image: {spec.id}')
                await self._pull_with_progress_logging(podman_client, spec.id)
                _logger.info(f'⬇️  Finished Pulling Podman Image: {spec.id}')
        except docker.errors.APIError as exc:
            raise SandboxError(f'Error Getting Podman Image: {spec.id}') from exc

    async def _pull_with_progress_logging(
        self, podman_client: docker.DockerClient, image_id: str
    ):
        pull_complete = asyncio.Event()

        async def periodic_logger():
            while not pull_complete.is_set():
                try:
                    await asyncio.wait_for(pull_complete.wait(), timeout=5.0)
                    break
                except asyncio.TimeoutError:
                    _logger.info(f'🔄 Downloading Podman Image: {image_id}...')

        async def pull_image():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, podman_client.images.pull, image_id)
            finally:
                pull_complete.set()

        logger_task = asyncio.create_task(periodic_logger())
        pull_task = asyncio.create_task(pull_image())

        try:
            await pull_task
        finally:
            if not logger_task.done():
                logger_task.cancel()
                try:
                    await logger_task
                except asyncio.CancelledError:
                    pass
