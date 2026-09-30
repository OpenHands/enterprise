"""The lifecycle jobs, against Docker, E2B and k8s agent-sandbox.

The providers are the fakes from the lifecycle contract tests. The rows are
real, and so is the row locking.
"""

import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select, text

from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxServiceInjector,
)
from openhands.app_server.sandbox.lifecycle import tasks
from openhands.app_server.sandbox.lifecycle.rules import Action, Reason
from openhands.app_server.sandbox.lifecycle.settings import (
    SandboxLifecycleOverrides,
    SandboxLifecycleSettings,
)
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    LifecycleState,
    StoredSandbox,
)
from openhands.app_server.worker.app import app
from tests.unit.app_server.test_sandbox_lifecycle_contract import (
    DockerHarness,
    E2BHarness,
    K8sHarness,
)

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


class FakeAgentServer:
    """httpx.AsyncClient stand in that answers ``GET /server_info``."""

    def __init__(self, idle_time: float = 0, error: Exception | None = None):
        self.idle_time = idle_time
        self.error = error
        self.urls: list[str] = []

    async def get(self, url: str, **kwargs) -> httpx.Response:
        self.urls.append(url)
        if self.error:
            raise self.error
        return httpx.Response(
            200,
            json={'uptime': 0, 'idle_time': self.idle_time},
            request=httpx.Request('GET', url),
        )


@pytest.fixture(params=['docker', 'e2b', 'k8s-agent-sandbox'])
def harness(request):
    if request.param == 'docker':
        yield DockerHarness()
    elif request.param == 'e2b':
        e2b = E2BHarness()
        try:
            yield e2b
        finally:
            e2b.close()
    else:
        yield K8sHarness()


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def add_sandbox(harness, db_session):
    """Commit a sandbox's row, and give the provider its live state."""

    async def _add(row_state: LifecycleState, live_paused: bool, ago: timedelta):
        row = harness.row()
        row.lifecycle_state = row_state
        row.state_changed_at = row.last_active_at = datetime.now(UTC) - ago
        db_session.add(row)
        await db_session.commit()
        harness.set_live(paused=live_paused)
        if isinstance(harness, DockerHarness):
            # Docker reads the agent server's URL off the port binding.
            harness.containers[harness.sandbox_id].attrs['NetworkSettings'] = {
                'Ports': {'8000/tcp': [{'HostPort': '12345'}]}
            }
        return row

    return _add


async def _check(
    harness,
    db_session,
    agent_server: FakeAgentServer,
    spec_lifecycle: SandboxLifecycleOverrides | None = None,
):
    row = harness.row()
    spec = SandboxSpecInfo(
        id=row.sandbox_spec_id, command=None, lifecycle=spec_lifecycle
    )
    return await tasks.check_sandbox(
        harness.sandbox_id,
        backend=row.backend,
        defaults=SandboxLifecycleSettings(),
        db_session=db_session,
        sandbox_service=harness.service(db_session),
        sandbox_spec_service=PresetSandboxSpecService(specs=[spec]),
        httpx_client=agent_server,  # type: ignore[arg-type]
        now=datetime.now(UTC),
    )


async def _reload(db_session, sandbox_id: str) -> StoredSandbox | None:
    await db_session.flush()
    db_session.expunge_all()
    result = await db_session.execute(
        select(StoredSandbox).where(StoredSandbox.id == sandbox_id)
    )
    return result.scalar_one_or_none()


async def _live_status(harness, db_session) -> SandboxStatus | None:
    sandbox = await harness.service(db_session).get_sandbox(harness.sandbox_id)
    return sandbox.status if sandbox else None


class TestCheck:
    async def test_an_idle_sandbox_is_paused(self, harness, db_session, add_sandbox):
        await add_sandbox(LifecycleState.RUNNING, live_paused=False, ago=2 * HOUR)
        agent_server = FakeAgentServer(idle_time=30 * 60)

        decision = await _check(harness, db_session, agent_server)

        assert decision.action == Action.PAUSE
        assert decision.reason == Reason.IDLE
        assert agent_server.urls[0].endswith('/server_info')
        row = await _reload(db_session, harness.sandbox_id)
        assert row.lifecycle_state == LifecycleState.PAUSED
        assert await _live_status(harness, db_session) == SandboxStatus.PAUSED

    async def test_a_working_sandbox_has_its_activity_recorded(
        self, harness, db_session, add_sandbox
    ):
        await add_sandbox(LifecycleState.RUNNING, live_paused=False, ago=2 * HOUR)
        before = datetime.now(UTC)

        decision = await _check(harness, db_session, FakeAgentServer(idle_time=60))

        assert decision.action == Action.RECORD_ACTIVITY
        row = await _reload(db_session, harness.sandbox_id)
        assert row.lifecycle_state == LifecycleState.RUNNING
        assert row.last_active_at >= before - timedelta(seconds=61)
        assert await _live_status(harness, db_session) == SandboxStatus.RUNNING

    async def test_the_max_session_pauses_a_working_sandbox(
        self, harness, db_session, add_sandbox
    ):
        await add_sandbox(LifecycleState.RUNNING, live_paused=False, ago=13 * HOUR)
        agent_server = FakeAgentServer(idle_time=0)

        decision = await _check(harness, db_session, agent_server)

        assert decision.reason == Reason.MAX_SESSION
        assert agent_server.urls == []
        assert await _live_status(harness, db_session) == SandboxStatus.PAUSED

    async def test_an_unreachable_agent_server_changes_nothing(
        self, harness, db_session, add_sandbox
    ):
        added = await add_sandbox(
            LifecycleState.RUNNING, live_paused=False, ago=2 * HOUR
        )
        agent_server = FakeAgentServer(error=httpx.ConnectError('refused'))

        decision = await _check(harness, db_session, agent_server)

        assert decision.action == Action.NOTHING
        row = await _reload(db_session, harness.sandbox_id)
        assert row.last_active_at == added.last_active_at
        assert await _live_status(harness, db_session) == SandboxStatus.RUNNING

    async def test_a_spec_is_held_to_its_own_settings(
        self, harness, db_session, add_sandbox
    ):
        await add_sandbox(LifecycleState.RUNNING, live_paused=False, ago=2 * HOUR)

        decision = await _check(
            harness,
            db_session,
            FakeAgentServer(idle_time=120),
            spec_lifecycle=SandboxLifecycleOverrides(idle_seconds=60),
        )

        assert decision.action == Action.PAUSE

    async def test_a_pause_the_provider_made_is_recorded(
        self, harness, db_session, add_sandbox
    ):
        await add_sandbox(LifecycleState.RUNNING, live_paused=True, ago=2 * HOUR)

        decision = await _check(harness, db_session, FakeAgentServer())

        assert decision.action == Action.RECORD_PAUSE
        row = await _reload(db_session, harness.sandbox_id)
        assert row.lifecycle_state == LifecycleState.PAUSED

    async def test_a_sandbox_paused_for_ten_days_is_deleted(
        self, harness, db_session, add_sandbox
    ):
        await add_sandbox(LifecycleState.PAUSED, live_paused=True, ago=10 * DAY)

        decision = await _check(harness, db_session, FakeAgentServer())

        assert decision.action == Action.DELETE
        assert await _reload(db_session, harness.sandbox_id) is None
        assert harness.provider_calls() > 0

    async def test_a_sandbox_changing_right_now_is_left_alone(
        self, harness, db_session, add_sandbox, async_session_maker
    ):
        await add_sandbox(LifecycleState.PAUSED, live_paused=True, ago=10 * DAY)

        async with async_session_maker() as other_session:
            await other_session.execute(
                text('SELECT id FROM v1_remote_sandbox WHERE id = :id FOR UPDATE'),
                {'id': harness.sandbox_id},
            )
            decision = await _check(harness, db_session, FakeAgentServer())

        assert decision is None
        assert harness.provider_calls() == 0

    async def test_a_sandbox_without_a_row_is_left_alone(self, harness, db_session):
        assert await _check(harness, db_session, FakeAgentServer()) is None


class TestSweep:
    @pytest.fixture
    def connector(self):
        connector = InMemoryConnector()
        with app.replace_connector(connector):
            yield connector

    def _queued(self, connector) -> list[str]:
        return sorted(job['args']['sandbox_id'] for job in connector.jobs.values())

    async def test_runs_every_minute(self):
        schedules = {
            periodic_task.task.name: periodic_task.cron
            for periodic_task in app.periodic_registry.periodic_tasks.values()
        }

        assert schedules['sandbox_lifecycle:sweep'] == '* * * * *'

    async def test_queues_one_check_per_sandbox(self, connector):
        await tasks.queue_checks(['sb-1', 'sb-2'])
        await tasks.queue_checks(['sb-1', 'sb-3'])

        assert self._queued(connector) == ['sb-1', 'sb-2', 'sb-3']
        assert {job['task_name'] for job in connector.jobs.values()} == {
            'sandbox_lifecycle:check'
        }

    async def test_queues_checks_for_the_due_sandboxes(self, connector, db_session):
        now = datetime.now(UTC)
        for sandbox_id, ago in [('due', 2 * HOUR), ('active', timedelta())]:
            db_session.add(
                StoredSandbox(
                    id=sandbox_id,
                    backend=DOCKER_BACKEND,
                    sandbox_spec_id='spec',
                    state_changed_at=now - ago,
                    last_active_at=now - ago,
                )
            )
        await db_session.flush()

        @contextlib.asynccontextmanager
        async def _services():
            yield db_session, None, PresetSandboxSpecService(specs=[]), None

        with (
            patch.object(
                tasks, '_managed_backend', return_value=DockerSandboxServiceInjector()
            ),
            patch.object(tasks, '_services', _services),
        ):
            await tasks.sweep(None, 0)

        assert self._queued(connector) == ['due']

    async def test_does_nothing_for_backends_runtime_api_manages(self, connector):
        config = SimpleNamespace(
            sandbox=RemoteSandboxServiceInjector(
                api_url='http://runtime-api', api_key='key'
            )
        )

        with patch(
            'openhands.app_server.config.get_global_config', return_value=config
        ):
            await tasks.sweep(None, 0)
            await tasks.check('sb-1')

        assert connector.jobs == {}
