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

DEFAULT_TEMPLATE_NAME = 'openhands-agent-server'
DEFAULT_WORKING_DIR = '/workspace/project'
DEFAULT_STATE_DIR = '/var/openhands'
DEFAULT_AGENT_SERVER_PORT = 8000
DEFAULT_VSCODE_PORT = 8001


class SubstrateSandboxSpecInfo(SandboxSpecInfo):
    """A sandbox spec whose ``id`` is a Substrate ActorTemplate name.

    The operator registers the template in the app's atespace ahead of time,
    and the app creates actors from it. The Settings dropdown labels each
    option with ``spec.id``, so templates appear there with no frontend change.

    ``initial_env`` must hold only values that are safe to publish: the public
    ``GET /api/v1/sandbox-specs/search`` endpoint serializes it verbatim.
    Credentials belong in the ``/api/init`` body that
    ``SubstrateSandboxService`` builds per sandbox.
    """

    init_api_key: SecretStr | None = Field(
        default=None,
        description=(
            'The OH_SECRET_KEY the ActorTemplate boots the agent server with, '
            'sent as the X-Init-API-Key header on POST /api/init.'
        ),
    )
    agent_server_port: int = Field(
        default=DEFAULT_AGENT_SERVER_PORT,
        description='Port the agent server listens on inside the actor',
    )
    vscode_port: int = Field(
        default=DEFAULT_VSCODE_PORT,
        description='Port the VS Code server listens on inside the actor',
    )
    state_dir: str = Field(
        default=DEFAULT_STATE_DIR,
        description=(
            "Directory for the agent server's own state (conversations, bash "
            'events), outside the workspace, so an agent serving its workspace '
            'on the app ports cannot publish it (OHE-3812).'
        ),
    )


def _default_init_api_key() -> SecretStr | None:
    init_api_key = os.getenv('SUBSTRATE_INIT_API_KEY')
    return SecretStr(init_api_key) if init_api_key else None


def get_default_sandbox_specs() -> list[SubstrateSandboxSpecInfo]:
    """The single ActorTemplate the backend ships with.

    ``command`` is None because the template carries the container's command.
    """
    return [
        SubstrateSandboxSpecInfo(
            id=os.getenv('SUBSTRATE_ACTOR_TEMPLATE') or DEFAULT_TEMPLATE_NAME,
            command=None,
            working_dir=DEFAULT_WORKING_DIR,
            init_api_key=_default_init_api_key(),
        )
    ]


class SubstrateSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    """Dependency injector for Substrate sandbox spec services."""

    specs: list[SubstrateSandboxSpecInfo] = Field(
        default_factory=get_default_sandbox_specs,
        description='Preset list of ActorTemplates offered as sandbox specs',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        specs: list[SandboxSpecInfo] = list(self.specs)
        yield PresetSandboxSpecService(specs=specs)
