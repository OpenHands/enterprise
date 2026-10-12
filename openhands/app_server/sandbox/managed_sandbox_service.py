"""The base for backends whose sandboxes the app pauses, resumes and deletes.

Each sandbox has a row in the sandbox table, which records what the app last
did to it. This base class owns that row. Every transition locks the row,
calls the backend's provider hook, and then updates the row from what the hook
reports. A backend only talks to its provider.

The background worker applies the lifecycle rules through the same interface
(see ``openhands.app_server.sandbox.lifecycle``).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, ClassVar

import httpx
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.env_parser import from_env
from openhands.agent_server.utils import utc_now
from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
from openhands.app_server.sandbox.sandbox_models import SandboxInfo
from openhands.app_server.sandbox.sandbox_service import (
    SandboxService,
    SandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_store import (
    LifecycleState,
    StoredSandbox,
    get_stored_sandbox,
    mark_paused,
    mark_running,
)
from openhands.app_server.user.user_context import UserContext

_logger = logging.getLogger(__name__)

# The agent server caps a foreground terminal command below this, so that one
# long command does not look like an idle sandbox.
RUNTIME_IDLE_TIMEOUT_VARIABLE = 'OH_RUNTIME_IDLE_TIMEOUT_SECONDS'
PROBE_TIMEOUT_SECONDS = 6


class ProviderOutcome(Enum):
    """What a provider hook did to a sandbox."""

    # The provider paused or resumed the sandbox.
    CHANGED = auto()
    # The sandbox was already paused, or already running.
    ALREADY_DONE = auto()
    # The sandbox is starting or broken, so there was nothing to do. The row is
    # left as it is.
    SKIPPED = auto()
    # The sandbox is gone from the provider, or the provider refused.
    FAILED = auto()


@dataclass
class ManagedSandboxService(SandboxService, ABC):
    """A sandbox service whose rows record each sandbox's lifecycle state."""

    backend: ClassVar[str]

    user_context: UserContext
    db_session: AsyncSession
    httpx_client: httpx.AsyncClient
    max_num_sandboxes: int
    lifecycle: SandboxLifecycleSettings = field(
        default_factory=SandboxLifecycleSettings, kw_only=True
    )

    async def _get_stored_sandbox(
        self, sandbox_id: str, for_update: bool = False
    ) -> StoredSandbox | None:
        """Get a sandbox row, or None when the caller may not see it."""
        return await get_stored_sandbox(
            self.db_session,
            self.user_context,
            self.backend,
            sandbox_id,
            for_update=for_update,
        )

    def _new_stored_sandbox(self, **fields: Any) -> StoredSandbox:
        """A row for a sandbox this backend is starting. The caller adds it."""
        stored_sandbox = StoredSandbox(
            backend=self.backend, created_at=utc_now(), **fields
        )
        mark_running(stored_sandbox)
        return stored_sandbox

    def _lifecycle_env(self) -> dict[str, str]:
        """Env for a new agent server, from the lifecycle settings."""
        if not self.lifecycle.idle_seconds:
            return {}
        return {RUNTIME_IDLE_TIMEOUT_VARIABLE: str(self.lifecycle.idle_seconds)}

    async def get_idle_time(self, sandbox: SandboxInfo) -> float | None:
        """How long the agent server has done nothing, by its own count.

        None when the agent server cannot be read, which changes nothing: a slow
        or restarting agent server is not an idle one.
        """
        try:
            url = self._get_agent_server_url(sandbox)
            response = await self.httpx_client.get(
                f'{url.rstrip("/")}/server_info', timeout=PROBE_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return float(response.json()['idle_time'])
        except (SandboxError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            _logger.info(
                'sandbox_lifecycle.probe_failed',
                extra={'sandbox_id': sandbox.id, 'error': str(exc)},
            )
            return None

    async def resume_sandbox(self, sandbox_id: str) -> bool:
        # Pausing old sandboxes locks their rows, so it runs before this row is
        # locked. Otherwise two resumes of running sandboxes could each hold
        # one row while waiting for the other's. The sandbox being resumed is
        # left out: if already running, it would count toward the limit and be
        # paused by its own resume.
        await self.pause_old_sandboxes(
            self.max_num_sandboxes - 1, exclude_id=sandbox_id
        )

        stored_sandbox = await self._get_stored_sandbox(sandbox_id, for_update=True)
        if stored_sandbox is None:
            return False
        outcome = await self._resume_at_provider(stored_sandbox)
        if outcome == ProviderOutcome.FAILED:
            return False
        if outcome == ProviderOutcome.CHANGED or (
            outcome == ProviderOutcome.ALREADY_DONE
            and stored_sandbox.lifecycle_state != LifecycleState.RUNNING
        ):
            mark_running(stored_sandbox)
        return True

    async def pause_sandbox(self, sandbox_id: str) -> bool:
        stored_sandbox = await self._get_stored_sandbox(sandbox_id, for_update=True)
        if stored_sandbox is None:
            return False
        outcome = await self._pause_at_provider(stored_sandbox)
        if outcome == ProviderOutcome.FAILED:
            return False
        if outcome in (ProviderOutcome.CHANGED, ProviderOutcome.ALREADY_DONE):
            mark_paused(stored_sandbox)
        return True

    async def delete_sandbox(self, sandbox_id: str) -> bool:
        """Delete a sandbox and its row.

        Returns False only when there is no such sandbox or the caller may not
        see it. A failure at the provider raises ``SandboxDeleteRetryError``
        and keeps the row, so a live sandbox is never reported as gone.
        """
        stored_sandbox = await self._get_stored_sandbox(sandbox_id, for_update=True)
        if stored_sandbox is None:
            return False
        await self._delete_at_provider(stored_sandbox)
        await self.db_session.delete(stored_sandbox)
        return True

    @abstractmethod
    async def _resume_at_provider(
        self, stored_sandbox: StoredSandbox
    ) -> ProviderOutcome:
        """Resume the sandbox at the provider."""

    @abstractmethod
    async def _pause_at_provider(
        self, stored_sandbox: StoredSandbox
    ) -> ProviderOutcome:
        """Pause the sandbox at the provider."""

    @abstractmethod
    async def _delete_at_provider(self, stored_sandbox: StoredSandbox) -> None:
        """Delete the sandbox at the provider.

        A sandbox the provider no longer has counts as deleted. Raises
        ``SandboxDeleteRetryError`` when the provider fails.
        """


class ManagedSandboxServiceInjector(SandboxServiceInjector, ABC):
    """The injector for a ``ManagedSandboxService``.

    The background worker only acts when the configured backend's injector is
    one of these. It applies the rules in ``lifecycle`` to the backend's rows.
    """

    backend: ClassVar[str]
    lifecycle: SandboxLifecycleSettings = Field(
        # The legacy RUNTIME setting builds the injector without reading env.
        default_factory=lambda: from_env(
            SandboxLifecycleSettings, 'OH_SANDBOX_LIFECYCLE'
        ),
        description=(
            "When the app pauses and deletes this backend's sandboxes. "
            'Configure via OH_SANDBOX_LIFECYCLE_IDLE_SECONDS, '
            'OH_SANDBOX_LIFECYCLE_MAX_SESSION_SECONDS and '
            'OH_SANDBOX_LIFECYCLE_DELETE_AFTER_SECONDS.'
        ),
    )
