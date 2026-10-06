"""The lifecycle rules, one case at a time."""

from datetime import UTC, datetime, timedelta

import pytest

from openhands.app_server.sandbox.lifecycle.rules import (
    NOTHING,
    Action,
    Decision,
    Reason,
    decide,
)
from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_store import LifecycleState, StoredSandbox

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

DEFAULTS = SandboxLifecycleSettings()
RUNNING = SandboxStatus.RUNNING
PAUSED = SandboxStatus.PAUSED
ERROR = SandboxStatus.ERROR


def _row(
    state: LifecycleState = LifecycleState.RUNNING,
    changed: timedelta = 2 * HOUR,
    active: timedelta | None = None,
) -> StoredSandbox:
    """A row whose state changed, and agent was last active, that long ago."""
    return StoredSandbox(
        id='sb-1',
        lifecycle_state=state,
        state_changed_at=NOW - changed,
        last_active_at=NOW - (changed if active is None else active),
    )


def _decide(row, live_status, idle_time=None, settings=DEFAULTS) -> Decision:
    return decide(row, live_status, idle_time, settings, NOW)


class TestRunning:
    def test_an_idle_agent_is_paused(self):
        decision = _decide(_row(), RUNNING, idle_time=25 * 60)

        assert decision.action == Action.PAUSE
        assert decision.reason == Reason.IDLE
        assert decision.last_active_at == NOW - 25 * MINUTE

    def test_a_working_agent_has_its_activity_recorded(self):
        decision = _decide(_row(), RUNNING, idle_time=60)

        assert decision == Decision(Action.RECORD_ACTIVITY, last_active_at=NOW - MINUTE)

    def test_activity_the_row_already_has_changes_nothing(self):
        row = _row(active=MINUTE)

        assert _decide(row, RUNNING, idle_time=5 * 60).action == Action.NOTHING

    def test_a_resumed_sandbox_gets_a_full_idle_period(self):
        """The agent server's idle clock kept counting while it was paused."""
        row = _row(changed=5 * MINUTE, active=3 * DAY)

        assert _decide(row, RUNNING, idle_time=3 * 24 * 3600).action == Action.NOTHING

    def test_recorded_activity_counts_as_well(self):
        """The agent server restarted, so its own clock starts from zero."""
        row = _row(active=5 * MINUTE)

        assert _decide(row, RUNNING, idle_time=30 * 60).action == Action.NOTHING

    def test_a_failed_probe_changes_nothing(self):
        row = _row(changed=5 * HOUR)

        assert _decide(row, RUNNING, idle_time=None).action == Action.NOTHING

    def test_the_max_session_pauses_a_working_agent(self):
        decision = _decide(_row(changed=12 * HOUR, active=0 * MINUTE), RUNNING, 0)

        assert decision == Decision(Action.PAUSE, Reason.MAX_SESSION)

    def test_the_max_session_needs_no_probe(self):
        decision = _decide(_row(changed=13 * HOUR), RUNNING, idle_time=None)

        assert decision == Decision(Action.PAUSE, Reason.MAX_SESSION)

    def test_a_running_sandbox_is_never_deleted(self):
        row = _row(changed=30 * DAY)
        settings = SandboxLifecycleSettings(idle_seconds=0, max_session_seconds=0)

        assert _decide(row, RUNNING, None, settings).action == Action.NOTHING


class TestBroken:
    """The row says running, but the provider reports ERROR."""

    def test_it_is_paused_once_its_recorded_activity_is_idle(self):
        decision = _decide(_row(changed=5 * HOUR, active=25 * MINUTE), ERROR)

        assert decision == Decision(Action.PAUSE, Reason.IDLE, NOW - 25 * MINUTE)

    def test_the_max_session_pauses_it(self):
        decision = _decide(_row(changed=13 * HOUR, active=0 * MINUTE), ERROR)

        assert decision == Decision(Action.PAUSE, Reason.MAX_SESSION)

    def test_recent_activity_keeps_it(self):
        row = _row(changed=5 * HOUR, active=5 * MINUTE)

        assert _decide(row, ERROR) == NOTHING

    def test_a_resumed_sandbox_gets_a_full_idle_period(self):
        row = _row(changed=5 * MINUTE, active=3 * DAY)

        assert _decide(row, ERROR) == NOTHING

    def test_the_delete_comes_first(self):
        decision = _decide(_row(changed=10 * DAY), ERROR)

        assert decision == Decision(Action.DELETE, Reason.INACTIVE)

    def test_a_paused_row_is_not_paused_again(self):
        row = _row(LifecycleState.PAUSED, changed=13 * HOUR)

        assert _decide(row, ERROR) == NOTHING

    def test_zero_turns_both_rules_off(self):
        settings = SandboxLifecycleSettings(idle_seconds=0, max_session_seconds=0)

        assert _decide(_row(changed=13 * HOUR), ERROR, settings=settings) == NOTHING


class TestNotRunning:
    def test_a_pause_by_the_provider_is_recorded(self):
        decision = _decide(_row(), PAUSED)

        assert decision == Decision(Action.RECORD_PAUSE)

    def test_it_is_recorded_before_any_delete(self):
        assert _decide(_row(changed=30 * DAY), PAUSED).action == Action.RECORD_PAUSE

    @pytest.mark.parametrize(
        'live_status',
        [
            SandboxStatus.PAUSED,
            SandboxStatus.ERROR,
            SandboxStatus.STARTING,
            SandboxStatus.MISSING,
        ],
    )
    def test_a_sandbox_that_has_not_run_for_ten_days_is_deleted(self, live_status):
        row = _row(LifecycleState.PAUSED, changed=10 * DAY)

        assert _decide(row, live_status) == Decision(Action.DELETE, Reason.INACTIVE)

    def test_a_younger_one_is_kept(self):
        row = _row(LifecycleState.PAUSED, changed=9 * DAY)

        assert _decide(row, PAUSED).action == Action.NOTHING

    def test_activity_after_the_pause_counts(self):
        row = _row(LifecycleState.PAUSED, changed=11 * DAY, active=2 * DAY)

        assert _decide(row, PAUSED).action == Action.NOTHING


class TestSettings:
    def test_zero_turns_the_idle_rule_off(self):
        settings = SandboxLifecycleSettings(idle_seconds=0)

        decision = _decide(_row(), RUNNING, 5 * 3600, settings)

        assert decision.action == Action.NOTHING

    def test_zero_turns_the_max_session_off(self):
        settings = SandboxLifecycleSettings(max_session_seconds=0)

        assert _decide(_row(changed=30 * HOUR), RUNNING, 0, settings).action == (
            Action.RECORD_ACTIVITY
        )

    def test_zero_turns_the_delete_off(self):
        settings = SandboxLifecycleSettings(delete_after_seconds=0)
        row = _row(LifecycleState.PAUSED, changed=100 * DAY)

        assert _decide(row, PAUSED, settings=settings).action == Action.NOTHING

    def test_the_defaults_match_runtime_api(self):
        assert DEFAULTS == SandboxLifecycleSettings(
            idle_seconds=20 * 60,
            max_session_seconds=12 * 3600,
            delete_after_seconds=10 * 24 * 3600,
        )
