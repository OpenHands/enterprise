"""The sweep query, against this test's own Postgres."""

from datetime import UTC, datetime, timedelta

import pytest

from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
from openhands.app_server.sandbox.lifecycle.sweep import find_due_sandbox_ids
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    E2B_BACKEND,
    LifecycleState,
    StoredSandbox,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
DEFAULTS = SandboxLifecycleSettings()


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def store(db_session):
    async def _store(
        sandbox_id: str,
        state: LifecycleState = LifecycleState.RUNNING,
        changed: timedelta = 2 * HOUR,
        active: timedelta | None = None,
        backend: str = DOCKER_BACKEND,
    ) -> None:
        db_session.add(
            StoredSandbox(
                id=sandbox_id,
                backend=backend,
                created_by_user_id='user-1',
                sandbox_spec_id='default-spec',
                lifecycle_state=state,
                state_changed_at=NOW - changed,
                last_active_at=NOW - (changed if active is None else active),
            )
        )
        await db_session.flush()

    return _store


async def _due(db_session, settings=DEFAULTS, limit=100):
    return await find_due_sandbox_ids(
        db_session, DOCKER_BACKEND, settings, now=NOW, limit=limit
    )


async def test_a_running_sandbox_is_due_once_its_activity_is_old(db_session, store):
    await store('stale', changed=2 * HOUR, active=25 * MINUTE)
    await store('fresh', changed=2 * HOUR, active=5 * MINUTE)
    await store('just-resumed', changed=5 * MINUTE, active=3 * DAY)

    assert await _due(db_session) == ['stale']


async def test_the_max_session_counts_from_the_last_start_or_resume(db_session, store):
    await store('long-session', changed=12 * HOUR, active=MINUTE)

    assert await _due(db_session) == ['long-session']


async def test_a_paused_sandbox_is_due_only_for_its_delete(db_session, store):
    await store('paused-recently', LifecycleState.PAUSED, changed=HOUR)
    await store('paused-long-ago', LifecycleState.PAUSED, changed=10 * DAY)

    assert await _due(db_session) == ['paused-long-ago']


async def test_only_the_configured_backends_rows(db_session, store):
    await store('docker', changed=2 * HOUR)
    await store('e2b', changed=2 * HOUR, backend=E2B_BACKEND)

    assert await _due(db_session) == ['docker']


async def test_nothing_is_due_with_every_rule_off(db_session, store):
    await store('running', changed=100 * DAY)
    await store('paused', LifecycleState.PAUSED, changed=100 * DAY)

    off = SandboxLifecycleSettings(
        idle_seconds=0, max_session_seconds=0, delete_after_seconds=0
    )

    assert await _due(db_session, settings=off) == []


async def test_the_longest_inactive_come_first_up_to_the_limit(db_session, store):
    await store('three-hours', changed=3 * HOUR)
    await store('one-hour', changed=HOUR)
    await store('two-hours', changed=2 * HOUR)

    assert await _due(db_session, limit=2) == ['three-hours', 'two-hours']
