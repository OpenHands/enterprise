"""The rules that pause and delete sandboxes.

``decide`` is pure: it takes what the check read about one sandbox and says
what to do. The rules copy runtime-api's:

- **Idle.** A running sandbox whose agent has done nothing for
  ``idle_seconds`` is paused.
- **Max session.** A running sandbox is paused ``max_session_seconds`` after it
  last started or resumed, even if its agent is still working.
- **Delete.** A sandbox that is not running, and has not run for
  ``delete_after_seconds``, is deleted.

A sandbox always counts as active for a full idle period after it starts or
resumes. The agent server's idle clock can include time spent paused, so it
would otherwise read as idle right after a resume.

A failed read never leads to a delete. A failed idle probe changes nothing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_store import LifecycleState, StoredSandbox


class Action(StrEnum):
    NOTHING = 'nothing'
    PAUSE = 'pause'
    DELETE = 'delete'
    # The provider paused the sandbox on its own, or someone did by hand.
    RECORD_PAUSE = 'record_pause'
    RECORD_ACTIVITY = 'record_activity'


class Reason(StrEnum):
    IDLE = 'idle'
    MAX_SESSION = 'max_session'
    INACTIVE = 'inactive'


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: Reason | None = None
    # When the agent was last active, as far as the check could tell.
    last_active_at: datetime | None = None


NOTHING = Decision(Action.NOTHING)


def _elapsed(seconds: int, since: datetime, now: datetime) -> bool:
    """Whether a rule of ``seconds`` has come due. 0 turns the rule off."""
    return seconds > 0 and now - since >= timedelta(seconds=seconds)


def max_session_due(
    row: StoredSandbox, settings: SandboxLifecycleSettings, now: datetime
) -> bool:
    return _elapsed(settings.max_session_seconds, row.state_changed_at, now)


def decide(
    row: StoredSandbox,
    live_status: SandboxStatus,
    idle_time: float | None,
    settings: SandboxLifecycleSettings,
    now: datetime,
) -> Decision:
    """What to do with one sandbox.

    ``idle_time`` is the agent server's own count of seconds since it last did
    anything, or None when it was not read or could not be.
    """
    if live_status == SandboxStatus.RUNNING:
        if max_session_due(row, settings, now):
            return Decision(Action.PAUSE, Reason.MAX_SESSION)
        if idle_time is None:
            return NOTHING
        recorded = max(row.state_changed_at, row.last_active_at)
        last_active_at = max(recorded, now - timedelta(seconds=idle_time))
        if _elapsed(settings.idle_seconds, last_active_at, now):
            return Decision(Action.PAUSE, Reason.IDLE, last_active_at)
        if last_active_at > recorded:
            return Decision(Action.RECORD_ACTIVITY, last_active_at=last_active_at)
        return NOTHING

    if (
        live_status == SandboxStatus.PAUSED
        and row.lifecycle_state != LifecycleState.PAUSED
    ):
        return Decision(Action.RECORD_PAUSE)

    # Paused, failed, stuck starting, or gone from the provider.
    last_ran_at = max(row.state_changed_at, row.last_active_at)
    if _elapsed(settings.delete_after_seconds, last_ran_at, now):
        return Decision(Action.DELETE, Reason.INACTIVE)
    return NOTHING
