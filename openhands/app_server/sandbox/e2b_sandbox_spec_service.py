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
DEFAULT_AGENT_SERVER_PORT = 8000
DEFAULT_VSCODE_PORT = 8001


class E2BSandboxSpecInfo(SandboxSpecInfo):
    """A sandbox spec whose ``id`` is an E2B template name.

    E2B creates sandboxes from a pre-built template rather than from an image
    reference, so ``id`` carries the template name (or alias) handed to
    ``AsyncSandbox.create``. The Settings dropdown labels each option with
    ``spec.id``, so templates appear there with no frontend change.

    ``initial_env`` must hold only values that are safe to publish: the public
    ``GET /api/v1/sandbox-specs/search`` endpoint serializes it verbatim.
    Credentials belong in the ``/api/init`` body that ``E2BSandboxService``
    builds per sandbox.
    """

    init_api_key: SecretStr | None = Field(
        default=None,
        description=(
            'The OH_SECRET_KEY the template boots with, sent as the '
            'X-Init-API-Key header on POST /api/init. None when the template '
            'boots without a secret key, in which case /api/init is open.'
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
    init_api_key = os.getenv('E2B_INIT_API_KEY')
    return SecretStr(init_api_key) if init_api_key else None


def get_default_sandbox_specs() -> list[E2BSandboxSpecInfo]:
    """The single template the E2B backend ships with.

    ``command`` is None because an E2B template carries its own start command
    (set at build time with ``set_start_cmd``).
    """
    return [
        E2BSandboxSpecInfo(
            id=os.getenv('E2B_TEMPLATE') or DEFAULT_TEMPLATE_NAME,
            command=None,
            working_dir=DEFAULT_WORKING_DIR,
            init_api_key=_default_init_api_key(),
        )
    ]


class E2BSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    """Dependency injector for E2B sandbox spec services."""

    specs: list[E2BSandboxSpecInfo] = Field(
        default_factory=get_default_sandbox_specs,
        description='Preset list of E2B templates offered as sandbox specs',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        specs: list[SandboxSpecInfo] = list(self.specs)
        yield PresetSandboxSpecService(specs=specs)
