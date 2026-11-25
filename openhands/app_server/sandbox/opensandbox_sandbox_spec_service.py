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

DEFAULT_WORKING_DIR = '/workspace/project'
DEFAULT_AGENT_SERVER_PORT = 8000
DEFAULT_VSCODE_PORT = 8001


class OpenSandboxSandboxSpecInfo(SandboxSpecInfo):
    """A sandbox spec whose ``id`` is an OpenSandbox template id.

    OpenSandbox restores each sandbox from a template's golden snapshot, so
    ``id`` is the ``tpl_...`` id that ``scripts/opensandbox/build_template.py``
    prints. The template boots the agent server in deferred-init mode, and
    ``OpenSandboxSandboxService`` sends the per-sandbox config on
    ``POST /api/init``.

    ``initial_env`` must hold only values that are safe to publish: the public
    ``GET /api/v1/sandbox-specs/search`` endpoint serializes it verbatim.
    """

    init_api_key: SecretStr | None = Field(
        default=None,
        description=(
            'The OH_SECRET_KEY the template boots with, sent as the '
            'X-Init-API-Key header on POST /api/init.'
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
    init_api_key = os.getenv('OPENSANDBOX_INIT_API_KEY')
    return SecretStr(init_api_key) if init_api_key else None


def get_default_sandbox_specs() -> list[OpenSandboxSandboxSpecInfo]:
    """The single template named by OPENSANDBOX_TEMPLATE_ID.

    ``command`` is None because the template carries its own entrypoint.
    """
    template_id = os.getenv('OPENSANDBOX_TEMPLATE_ID')
    if not template_id:
        return []
    return [
        OpenSandboxSandboxSpecInfo(
            id=template_id,
            command=None,
            working_dir=DEFAULT_WORKING_DIR,
            init_api_key=_default_init_api_key(),
        )
    ]


class OpenSandboxSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    """Dependency injector for OpenSandbox sandbox spec services."""

    specs: list[OpenSandboxSandboxSpecInfo] = Field(
        default_factory=get_default_sandbox_specs,
        description='Preset list of OpenSandbox templates offered as sandbox specs',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        specs: list[SandboxSpecInfo] = list(self.specs)
        yield PresetSandboxSpecService(specs=specs)
