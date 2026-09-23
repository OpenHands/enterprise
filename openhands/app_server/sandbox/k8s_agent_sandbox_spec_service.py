import os
from typing import AsyncGenerator

from fastapi import Request
from pydantic import Field, SecretStr

from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    SandboxSpecServiceInjector,
)
from openhands.app_server.services.injector import InjectorState

DEFAULT_WARM_POOL_NAME = 'openhands-agent-server'
DEFAULT_WORKING_DIR = '/workspace/project'
DEFAULT_AGENT_SERVER_PORT = 8000
DEFAULT_VSCODE_PORT = 8001


class K8sAgentSandboxSpecInfo(SandboxSpecInfo):
    """A sandbox spec whose ``id`` is a SandboxWarmPool name.

    The operator creates the pool and its SandboxTemplate ahead of time, and the
    app only claims from it: ``id`` is the name a SandboxClaim puts in
    ``spec.warmPoolRef``. The Settings dropdown labels each option with
    ``spec.id``, so pools appear there with no frontend change.

    ``initial_env`` must hold only values that are safe to publish: the public
    ``GET /api/v1/sandbox-specs/search`` endpoint serializes it verbatim.
    Credentials belong in the ``/api/init`` body that ``K8sAgentSandboxService``
    builds per sandbox.
    """

    init_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "The OH_SECRET_KEY the pool's SandboxTemplate boots the agent server "
            'with, sent as the X-Init-API-Key header on POST /api/init.'
        ),
    )
    agent_server_port: int = Field(
        default=DEFAULT_AGENT_SERVER_PORT,
        description='Port the agent server listens on inside the sandbox',
    )
    vscode_port: int = Field(
        default=DEFAULT_VSCODE_PORT,
        description='Port openvscode-server listens on inside the sandbox',
    )


def _default_init_api_key() -> SecretStr | None:
    init_api_key = os.getenv('AGENT_SANDBOX_INIT_API_KEY')
    return SecretStr(init_api_key) if init_api_key else None


def get_default_sandbox_specs() -> list[K8sAgentSandboxSpecInfo]:
    """The single warm pool the backend ships with.

    ``command`` is None because the pool's SandboxTemplate carries the pod's
    command.
    """
    return [
        K8sAgentSandboxSpecInfo(
            id=os.getenv('AGENT_SANDBOX_WARM_POOL') or DEFAULT_WARM_POOL_NAME,
            command=None,
            working_dir=DEFAULT_WORKING_DIR,
            init_api_key=_default_init_api_key(),
        )
    ]


class K8sAgentSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    """Dependency injector for k8s agent-sandbox spec services."""

    specs: list[K8sAgentSandboxSpecInfo] = Field(
        default_factory=get_default_sandbox_specs,
        description='Preset list of SandboxWarmPools offered as sandbox specs',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        specs: list[SandboxSpecInfo] = list(self.specs)
        yield PresetSandboxSpecService(specs=specs)
