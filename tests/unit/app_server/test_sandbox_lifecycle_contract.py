"""The lifecycle contract every backend that stores sandbox rows keeps.

Each backend records starts, resumes and pauses on the sandbox's row, and
locks the row for every transition. The same tests run against Docker, E2B and
k8s agent-sandbox. The providers are faked; the rows are real.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import APIError, NotFound
from e2b import SandboxException, SandboxState
from sqlalchemy import select, text

from openhands.app_server.sandbox import e2b_sandbox_service
from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxService,
    ExposedPort,
)
from openhands.app_server.sandbox.sandbox_models import AGENT_SERVER
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    LifecycleState,
    StoredSandbox,
)
from tests.unit.app_server import test_e2b_sandbox_service as e2b_tests
from tests.unit.app_server import test_k8s_agent_sandbox_service as k8s_tests

OWNER_ID = 'user-1'
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def _user_context() -> AsyncMock:
    context = AsyncMock()
    context.get_user_id.return_value = OWNER_ID
    context.get_default_sandbox_spec_id.return_value = None
    return context


# ---------------------------------------------------------------------------
# One harness per backend
# ---------------------------------------------------------------------------


class FakeContainer:
    """A container as far as the Docker backend reads and drives it."""

    def __init__(self, name: str, status: str = 'running'):
        self.name = name
        self.status = status
        self.calls = 0
        self.attrs: dict[str, Any] = {
            'Config': {'Env': [], 'WorkingDir': '/workspace'},
            'HostConfig': {},
            'NetworkSettings': {'Ports': {}},
            'State': {'StartedAt': datetime.now(UTC).isoformat()},
        }
        self.fail_with: Exception | None = None

    def _transition(self, status: str) -> None:
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        self.status = status

    def pause(self):
        self._transition('paused')

    def unpause(self):
        self._transition('running')

    def start(self):
        self._transition('running')

    def stop(self, timeout: int | None = None):
        self._transition('exited')

    def remove(self):
        self.calls += 1


@dataclass
class DockerHarness:
    sandbox_id: str = 'oh-test-abc123'
    containers: dict[str, FakeContainer] = field(default_factory=dict)

    def __post_init__(self):
        self.client = MagicMock()
        self.client.containers.get.side_effect = self._get
        self.client.containers.list.side_effect = lambda **kwargs: list(
            self.containers.values()
        )
        self.client.containers.run.side_effect = self._run

    def _get(self, name: str) -> FakeContainer:
        if name not in self.containers:
            raise NotFound(name)
        return self.containers[name]

    def _run(self, **kwargs) -> FakeContainer:
        container = FakeContainer(kwargs['name'])
        self.containers[container.name] = container
        return container

    def service(self, db_session) -> SandboxService:
        spec = MagicMock()
        spec.id = 'test-image:latest'
        spec.initial_env = {}
        spec.working_dir = '/workspace'
        spec_service = AsyncMock()
        spec_service.get_default_sandbox_spec.return_value = spec
        spec_service.get_sandbox_spec.return_value = spec
        return DockerSandboxService(
            sandbox_spec_service=spec_service,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(name=AGENT_SERVER, description='', container_port=8000)
            ],
            health_check_path=None,
            httpx_client=AsyncMock(),
            max_num_sandboxes=10,
            user_context=_user_context(),
            db_session=db_session,
            docker_client=self.client,
        )

    def row(self) -> StoredSandbox:
        return StoredSandbox(
            id=self.sandbox_id,
            backend=DOCKER_BACKEND,
            created_by_user_id=OWNER_ID,
            sandbox_spec_id='test-image:latest',
            created_at=LONG_AGO,
        )

    def set_live(self, paused: bool) -> None:
        self.containers[self.sandbox_id] = FakeContainer(
            self.sandbox_id, 'exited' if paused else 'running'
        )

    def break_transitions(self) -> None:
        self.containers[self.sandbox_id].fail_with = APIError('daemon error')

    def provider_calls(self) -> int:
        return sum(container.calls for container in self.containers.values())


class E2BHarness:
    sandbox_id = e2b_tests.SANDBOX_ID

    def __init__(self):
        self.states: dict[str, SandboxState] = {}
        self.sdk = e2b_tests._mock_sdk()
        self.sdk.get_info.side_effect = self._get_info
        self.sdk.connect.side_effect = self._set(SandboxState.RUNNING)
        self.sdk.pause.side_effect = self._set(SandboxState.PAUSED)
        self.sdk.create.side_effect = self._create
        self._patch = patch.object(e2b_sandbox_service, 'AsyncSandbox', self.sdk)
        self._patch.start()

    def close(self) -> None:
        self._patch.stop()

    async def _get_info(self, sandbox_id: str, **kwargs):
        if sandbox_id not in self.states:
            raise e2b_tests.SandboxNotFoundException(sandbox_id)
        return e2b_tests._e2b_info(sandbox_id, state=self.states[sandbox_id])

    def _set(self, state: SandboxState):
        async def _transition(sandbox_id: str, **kwargs):
            self.states[sandbox_id] = state

        return _transition

    async def _create(self, **kwargs):
        self.states[self.sandbox_id] = SandboxState.RUNNING
        return MagicMock(sandbox_id=self.sandbox_id)

    def service(self, db_session) -> SandboxService:
        return e2b_tests._service(db_session)

    def row(self) -> StoredSandbox:
        return e2b_tests._stored(created_at=LONG_AGO)

    def set_live(self, paused: bool) -> None:
        self.states[self.sandbox_id] = (
            SandboxState.PAUSED if paused else SandboxState.RUNNING
        )

    def break_transitions(self) -> None:
        self.sdk.connect.side_effect = SandboxException('service unavailable')
        self.sdk.pause.side_effect = SandboxException('service unavailable')

    def provider_calls(self) -> int:
        return (
            self.sdk.connect.await_count
            + self.sdk.pause.await_count
            + self.sdk.kill.await_count
        )


class K8sHarness:
    sandbox_id = k8s_tests.CLAIM_NAME

    def __init__(self):
        self.k8s = k8s_tests.FakeAgentSandbox()

    def service(self, db_session) -> SandboxService:
        return k8s_tests._service(db_session, self.k8s)

    def row(self) -> StoredSandbox:
        row = k8s_tests._stored()
        row.created_at = LONG_AGO
        return row

    def set_live(self, paused: bool) -> None:
        self.k8s.add_claim(ready=k8s_tests.SUSPENDED if paused else k8s_tests.READY)

    def break_transitions(self) -> None:
        # The Sandbox is gone from under its claim, so no mode can be set.
        self.k8s.sandboxes.clear()

    def provider_calls(self) -> int:
        return len(self.k8s.modes) + len(self.k8s.deleted)


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

    async def _add(
        row_state: LifecycleState, live_paused: bool, changed_at: datetime = LONG_AGO
    ) -> None:
        row = harness.row()
        row.lifecycle_state = row_state
        row.state_changed_at = changed_at
        row.last_active_at = changed_at
        db_session.add(row)
        await db_session.commit()
        harness.set_live(paused=live_paused)

    return _add


async def _reload(db_session, sandbox_id: str) -> StoredSandbox:
    """Read a row back from the database rather than the identity map."""
    await db_session.flush()
    db_session.expunge_all()
    result = await db_session.execute(
        select(StoredSandbox).where(StoredSandbox.id == sandbox_id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


async def test_start_records_a_running_sandbox(harness, db_session):
    before = datetime.now(UTC)

    sandbox = await harness.service(db_session).start_sandbox()

    row = await _reload(db_session, sandbox.id)
    assert row.lifecycle_state == LifecycleState.RUNNING
    assert row.state_changed_at >= before
    assert row.last_active_at == row.state_changed_at


async def test_pause_records_when_it_paused(harness, db_session, add_sandbox):
    await add_sandbox(LifecycleState.RUNNING, live_paused=False)
    before = datetime.now(UTC)

    assert await harness.service(db_session).pause_sandbox(harness.sandbox_id)

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == LifecycleState.PAUSED
    assert row.state_changed_at >= before
    assert row.last_active_at == LONG_AGO


async def test_pausing_a_paused_sandbox_keeps_its_pause_time(
    harness, db_session, add_sandbox
):
    await add_sandbox(LifecycleState.PAUSED, live_paused=True)

    assert await harness.service(db_session).pause_sandbox(harness.sandbox_id)

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == LifecycleState.PAUSED
    assert row.state_changed_at == LONG_AGO


async def test_pause_corrects_a_row_that_says_running(harness, db_session, add_sandbox):
    """The provider paused the sandbox on its own, or someone did by hand."""
    await add_sandbox(LifecycleState.RUNNING, live_paused=True)

    assert await harness.service(db_session).pause_sandbox(harness.sandbox_id)

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == LifecycleState.PAUSED


async def test_resume_records_when_it_resumed(harness, db_session, add_sandbox):
    await add_sandbox(LifecycleState.PAUSED, live_paused=True)
    before = datetime.now(UTC)

    assert await harness.service(db_session).resume_sandbox(harness.sandbox_id)

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == LifecycleState.RUNNING
    assert row.state_changed_at >= before
    assert row.last_active_at == row.state_changed_at


async def test_resuming_a_running_sandbox_leaves_its_row(
    harness, db_session, add_sandbox
):
    await add_sandbox(LifecycleState.RUNNING, live_paused=False)

    assert await harness.service(db_session).resume_sandbox(harness.sandbox_id)

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == LifecycleState.RUNNING
    assert row.state_changed_at == LONG_AGO
    assert row.last_active_at == LONG_AGO


async def test_resume_corrects_a_row_that_says_paused(harness, db_session, add_sandbox):
    """Someone resumed the sandbox outside the app."""
    await add_sandbox(LifecycleState.PAUSED, live_paused=False)
    before = datetime.now(UTC)

    assert await harness.service(db_session).resume_sandbox(harness.sandbox_id)

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == LifecycleState.RUNNING
    assert row.state_changed_at >= before


@pytest.mark.parametrize('transition', ['pause_sandbox', 'resume_sandbox'])
async def test_a_failed_transition_leaves_the_row(
    harness, db_session, add_sandbox, transition
):
    live_paused = transition == 'resume_sandbox'
    row_state = LifecycleState.PAUSED if live_paused else LifecycleState.RUNNING
    await add_sandbox(row_state, live_paused=live_paused)
    harness.break_transitions()

    method = getattr(harness.service(db_session), transition)
    assert await method(harness.sandbox_id) is False

    row = await _reload(db_session, harness.sandbox_id)
    assert row.lifecycle_state == row_state
    assert row.state_changed_at == LONG_AGO


@pytest.mark.parametrize(
    'transition', ['pause_sandbox', 'resume_sandbox', 'delete_sandbox']
)
async def test_transitions_wait_for_the_row_lock(
    harness, db_session, add_sandbox, async_session_maker, transition
):
    """A transition in flight elsewhere holds the row, and this one waits.

    It waits before it touches the provider, so two transitions of one
    sandbox never reach the provider together.
    """
    live_paused = transition == 'resume_sandbox'
    row_state = LifecycleState.PAUSED if live_paused else LifecycleState.RUNNING
    await add_sandbox(row_state, live_paused=live_paused)
    method = getattr(harness.service(db_session), transition)

    async with async_session_maker() as other_session:
        await other_session.execute(
            text('SELECT id FROM v1_remote_sandbox WHERE id = :id FOR UPDATE'),
            {'id': harness.sandbox_id},
        )
        pending = asyncio.create_task(method(harness.sandbox_id))
        await asyncio.sleep(0.5)

        assert not pending.done()
        assert harness.provider_calls() == 0

        await other_session.commit()

    assert await pending is True
    assert harness.provider_calls() > 0


async def test_rows_written_without_the_columns_read_as_running(db_session):
    """App servers on the previous release write rows without the columns."""
    before = datetime.now(UTC) - timedelta(seconds=5)
    await db_session.execute(
        text(
            'INSERT INTO v1_remote_sandbox (id, backend, sandbox_spec_id) '
            "VALUES ('sb-old', 'docker', 'spec')"
        )
    )

    row = await _reload(db_session, 'sb-old')

    assert row.lifecycle_state == LifecycleState.RUNNING
    assert row.state_changed_at >= before
    assert row.last_active_at >= before
