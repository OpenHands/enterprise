import asyncio
import importlib
import logging
import os
from abc import ABC, abstractmethod
from functools import cache

from openhands.agent_server import env_parser
from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.sandbox_spec_models import (
    SandboxSpecInfo,
    SandboxSpecInfoPage,
)
from openhands.app_server.services.injector import Injector
from openhands.sdk.utils.models import DiscriminatedUnionMixin

_DEFAULT_REPOSITORY = 'ghcr.io/openhands/agent-server'
_logger = logging.getLogger(__name__)


@cache
def _bundled_agent_server_version() -> str:
    """Version of the installed openhands-agent-server package.

    openhands-agent-server is a hard runtime dependency of the V1 app server;
    if it's not installed the V1 app server has no business starting. Let
    PackageNotFoundError surface at first call rather than degrading silently
    to a wrong default. Memoised via ``functools.cache`` so the lookup runs
    once per process and remains mockable in tests.
    """
    return importlib.metadata.version('openhands-agent-server')


def _bundled_default_image() -> str:
    """Image URL of the bundled agent-server: ``<repo>:<version>-python``."""
    return f'{_DEFAULT_REPOSITORY}:{_bundled_agent_server_version()}-python'


class SandboxSpecService(ABC):
    """Service for managing Sandbox specs.

    At present this is read only. The plan is that later this class will allow building
    and deleting sandbox specs and limiting access by user and group. It would also be
    nice to be able to set the desired number of warm sandboxes for a spec and scale
    this up and down.
    """

    @property
    def selection_enabled(self) -> bool:
        return False

    @property
    def strict_spec_eligibility(self) -> bool:
        """Whether reuse must hard-filter to specs this service still allows.

        ``False`` (the default) keeps the legacy best-effort behaviour: a
        sandbox on a spec the service can no longer resolve (e.g. a stale
        advertised image after an agent-server bump) is merely deprioritised,
        and reused anyway when nothing else is eligible. Services that return
        ``True`` have specs that can be actively disallowed for *new*
        launches (not merely absent from the catalog), so an ineligible spec
        must never be reused even when it is the only grouping candidate.
        """
        return False

    async def get_start_sandbox_spec(
        self, sandbox_spec_id: str
    ) -> SandboxSpecInfo | None:
        """Resolve a template allowed for a new sandbox in this request context."""
        return await self.get_sandbox_spec(sandbox_spec_id)

    @abstractmethod
    async def search_sandbox_specs(
        self, page_id: str | None = None, limit: int = 100
    ) -> SandboxSpecInfoPage:
        """Search for sandbox specs."""

    @abstractmethod
    async def get_sandbox_spec(self, sandbox_spec_id: str) -> SandboxSpecInfo | None:
        """Get a single sandbox spec, returning None if not found."""

    async def get_default_sandbox_spec(self) -> SandboxSpecInfo:
        """Get the default sandbox spec."""
        page = await self.search_sandbox_specs()
        if not page.items:
            raise SandboxError('No sandbox specs available!')
        return page.items[0]

    async def resolve_existing_sandbox_spec(
        self, sandbox_spec_id: str, sandbox_id: str
    ) -> SandboxSpecInfo:
        """Return the spec an existing sandbox was created from, or the default.

        Runtime-api advertises only the current agent-server image, so a sandbox
        created before an image bump has no spec of its own. Callers read only
        ``working_dir`` from the result, which the default is assumed to share; do
        not use this for image-specific fields (command, initial_env, run_as_user).
        Raises ``SandboxError`` when there is no default either.
        """
        spec = await self.get_sandbox_spec(sandbox_spec_id)
        if spec is not None:
            return spec
        spec = await self.get_default_sandbox_spec()
        _logger.warning(
            'Sandbox spec %s not found for sandbox %s; using default spec %s '
            '(working_dir=%s)',
            sandbox_spec_id,
            sandbox_id,
            spec.id,
            spec.working_dir,
        )
        return spec

    async def batch_get_sandbox_specs(
        self, sandbox_spec_ids: list[str]
    ) -> list[SandboxSpecInfo | None]:
        """Get a batch of sandbox specs, returning None for any not found."""
        results = await asyncio.gather(
            *[
                self.get_sandbox_spec(sandbox_spec_id)
                for sandbox_spec_id in sandbox_spec_ids
            ]
        )
        return results


class SandboxSpecServiceInjector(
    DiscriminatedUnionMixin, Injector[SandboxSpecService], ABC
):
    async def prepare(self) -> None:
        """Make the specs ready to use. The app runs this once, at startup,
        before it accepts requests."""


async def resolve_sandbox_spec(
    sandbox_spec_id: str | None,
    user_default_spec_id: str | None,
    sandbox_spec_service: SandboxSpecService,
    logger: logging.Logger,
) -> SandboxSpecInfo:
    """Return the SandboxSpecInfo to use for a new sandbox.

    Resolution order:
    1. ``sandbox_spec_id`` (caller-explicit) — not found is a hard error.
    2. ``user_default_spec_id`` (user preference) — if missing, log a warning
       and fall back to the system default.
    3. System default (first spec returned by the service).
    """
    from_user_default = sandbox_spec_id is None and user_default_spec_id is not None
    effective_id = (
        sandbox_spec_id if sandbox_spec_id is not None else user_default_spec_id
    )

    if effective_id is None:
        return await sandbox_spec_service.get_default_sandbox_spec()

    spec = await sandbox_spec_service.get_start_sandbox_spec(effective_id)
    if spec is not None:
        return spec

    if from_user_default:
        logger.warning(
            'User default sandbox spec %r not found; falling back to system default.',
            effective_id,
        )
        return await sandbox_spec_service.get_default_sandbox_spec()

    raise ValueError(f'Sandbox Spec {effective_id!r} not found')


@cache  # memoize so the auto-correct warning fires once per process
def get_agent_server_image() -> str:
    repository = os.getenv('AGENT_SERVER_IMAGE_REPOSITORY') or _DEFAULT_REPOSITORY
    bundled_version = _bundled_agent_server_version()
    tag = os.getenv('AGENT_SERVER_IMAGE_TAG') or f'{bundled_version}-python'

    if repository == _DEFAULT_REPOSITORY:
        parts = tag.split('-', 1)
        if len(parts) == 2 and parts[0] != bundled_version:
            parts[0] = bundled_version
            updated_tag = '-'.join(parts)
            logging.getLogger(__name__).warning(
                f'AGENT_SERVER_IMAGE_TAG={tag} does not match the installed '
                f'openhands-sdk (using {updated_tag} instead). Pin to a custom '
                f'image repository via AGENT_SERVER_IMAGE_REPOSITORY if you '
                f'need to use a different build.'
            )
            tag = updated_tag

    return f'{repository}:{tag}'


def is_custom_sandbox_spec(sandbox_spec_id: str) -> bool:
    """True when the sandbox spec differs from the bundled release default (e.g. a
    custom image registered via runtime-api). The bundled spec always matches the
    SDK the app server ships with, so non-default specs are the only ones worth
    flagging on a sandbox-create failure."""
    return sandbox_spec_id != _bundled_default_image()


def looks_like_image_reference(sandbox_spec_id: str) -> bool:
    """Best-effort, narrow check for whether ``sandbox_spec_id`` is itself an
    image reference rather than a named spec id.

    Legacy sandboxes (predating named specs) store the image URL directly as
    their ``sandbox_spec_id`` -- see ``RemoteSandboxSpecService``, whose single
    spec's ``id`` is ``get_agent_server_image()``, always of the form
    ``<repo-path>:<tag>`` (e.g. ``ghcr.io/openhands/agent-server:1.57.0-python``).
    This only recognizes that specific, agent-server-produced shape. A
    general image reference does not require a registry/namespace path or an
    explicit tag (e.g. ``ubuntu``, ``ubuntu:22.04`` both name real images),
    so this intentionally errs toward false negatives -- treating something
    as "unknown" (see callers) -- rather than guessing on an id that merely
    doesn't match this narrow shape.
    """
    return '/' in sandbox_spec_id and ':' in sandbox_spec_id


# Prefixes for environment variables that should be auto-forwarded to agent-server
# These are typically configuration variables that affect the agent's behavior
AUTO_FORWARD_PREFIXES = ('LLM_', 'LMNR_', 'OH_TELEMETRY_')


def get_agent_server_env() -> dict[str, str]:
    """Get environment variables to be injected into agent server sandbox environments.

    This function combines two sources of environment variables:

    1. **Auto-forwarded variables**: Environment variables with certain prefixes
       (e.g., LLM_*, LMNR_*) are automatically forwarded to the agent-server container.
       This ensures that LLM configuration like timeouts and retry settings
       work correctly in the two-container V1 architecture, as well as
       Laminar monitoring/analytics configuration.

    2. **Explicit overrides via OH_AGENT_SERVER_ENV**: A JSON string that allows
       setting arbitrary environment variables in the agent-server container.
       Values set here take precedence over auto-forwarded variables.

    Auto-forwarded prefixes:
        - LLM_* : LLM configuration (timeout, retries, model settings, etc.)
        - LMNR_* : Laminar monitoring/analytics configuration
        - OH_TELEMETRY_* : agent-server product telemetry configuration

    Usage:
        # Auto-forwarding (no action needed):
        export LLM_TIMEOUT=3600
        export LLM_NUM_RETRIES=10
        # These will automatically be available in the agent-server

        # Auto-forwarding for Laminar:
        export LMNR_PROJECT_API_KEY=your-api-key
        export LMNR_BASE_URL=https://app.lmnr.ai
        # These will automatically be available in the agent-server

        # Explicit override via JSON:
        OH_AGENT_SERVER_ENV='{"DEBUG": "true", "CUSTOM_VAR": "value"}'

        # Override an auto-forwarded variable:
        export LLM_TIMEOUT=3600  # Would be auto-forwarded as 3600
        OH_AGENT_SERVER_ENV='{"LLM_TIMEOUT": "7200"}'  # Overrides to 7200

    Returns:
        dict[str, str]: Dictionary of environment variable names to values.
                       Returns empty dict if no variables are found.

    Raises:
        JSONDecodeError: If OH_AGENT_SERVER_ENV contains invalid JSON.
    """
    result: dict[str, str] = {}

    # Step 1: Auto-forward environment variables with recognized prefixes
    for key, value in os.environ.items():
        if any(key.startswith(prefix) for prefix in AUTO_FORWARD_PREFIXES):
            result[key] = value

    # Step 2: Apply explicit overrides from OH_AGENT_SERVER_ENV
    # These take precedence over auto-forwarded variables
    explicit_env = env_parser.from_env(dict[str, str], 'OH_AGENT_SERVER_ENV')
    result.update(explicit_env)

    return result
