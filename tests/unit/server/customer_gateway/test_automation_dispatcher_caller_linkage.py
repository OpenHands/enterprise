"""Real dispatcher caller -> real terminal-status persistence linkage.

Adapted (not copied verbatim) from OpenHands/automation's own
tests/test_conversation_start.py, at the commit AUTOMATION_SOURCE_DIR is
pinned to -- see _backend_adapter.py's docstring and
.github/workflows/py-tests.yml for that checkout's provenance. What's
reused: the backend-mocking pattern
(``_backend_adapter``-equivalent ``MagicMock`` for ``ExecutionBackend``) and
the call shape into ``openhands.automation.dispatcher._execute_run`` -- the
real, unmodified caller test_automation_run_lifecycle.py's
``mark_run_terminal``/``mark_run_status`` tests do NOT exercise. That file's
own docstring says exactly this: "NOT established... the dispatcher's full
`_execute_run` control flow... is NOT exercised." This file closes that gap
for the specific claim Task 2 asks for: a launch/inference refusal reaching
the real dispatcher caller is actually persisted as FAILED, not just the
terminal-write helper called directly.

Real: ``openhands.automation.dispatcher._execute_run`` (unmodified, imported
from AUTOMATION_SOURCE_DIR), the real SQLAlchemy ORM models/session, and
(transitively) the real ``mark_run_terminal`` it calls on failure.

Mocked/stubbed (explicitly, no PG claim):
  - DB: in-memory SQLite (see test_automation_run_lifecycle.py's own
    docstring for why -- no Postgres server is available in this sandbox;
    this is a stand-in engine, the ORM/status-write code runs unmodified).
  - ``ExecutionBackend`` (sandbox provisioning / CloudSandboxBackend network
    calls): a ``MagicMock`` standing in for the whole backend, exactly as
    the reference test does -- ``backend.start_conversation`` raises the
    real ``ConversationStartError`` exception type to simulate an inference/
    launch refusal, or returns a real ``StartedConversation`` for the
    success case.
  - ``execute_in_context``/``upload_files`` (actual sandbox command
    execution and tarball upload): patched out at the dispatcher module
    level, same as the reference test -- no real sandbox command ever runs.
  - ``httpx.AsyncClient`` (``mock_client``): a bare MagicMock; no real HTTP.

NOT established: a live model provider, a live sandbox, or the entrypoint
script's own ``fire_callback`` HTTP round-trip (a separate, later leg not
reached by ``_execute_run`` itself for the refusal case below, since the
refusal happens before any command executes).
"""

from __future__ import annotations  # noqa: I001

import uuid
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from . import _backend_adapter as _ba  # noqa: F401 (openhands.__path__ side effect)

from openhands.automation.backends.base import (  # noqa: E402
    ExecutionContext,
    StartedConversation,
)
from openhands.automation.config import ServiceSettings  # noqa: E402
from openhands.automation.dispatcher import _execute_run  # noqa: E402
from openhands.automation.exceptions import ConversationStartError  # noqa: E402
from openhands.automation.models import (  # noqa: E402
    Automation,
    AutomationRun,
    AutomationRunStatus,
    Base,
)
from openhands.automation.utils import utcnow  # noqa: E402

TEST_USER_ID = uuid.uuid4()
TEST_ORG_ID = uuid.uuid4()
PROFILE_ID = '0a1b2c3d-0000-4000-8000-000000000001'
PROFILE_UUID = uuid.UUID(PROFILE_ID)


@pytest.fixture
async def session_factory():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_settings():
    return ServiceSettings()


@pytest.fixture
def mock_client():
    return MagicMock()


async def _make_run(session_factory, *, preset_metadata) -> AutomationRun:
    async with session_factory() as session:
        automation = Automation(
            user_id=TEST_USER_ID,
            created_by_user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            name='nightly',
            agent_profile_id=PROFILE_UUID,
            model='Default',
            preset_metadata=preset_metadata,
            trigger={'type': 'event', 'source': 'github-events', 'on': '*'},
            tarball_path='https://example.com/code.tar.gz',
            entrypoint='uv run main.py',
            enabled=True,
        )
        session.add(automation)
        await session.commit()
        run = AutomationRun(
            automation_id=automation.id,
            status=AutomationRunStatus.RUNNING,
            started_at=utcnow(),
        )
        session.add(run)
        await session.commit()
        run_id = run.id
    async with session_factory() as session:
        from sqlalchemy.orm import selectinload

        result = await session.execute(
            select(AutomationRun)
            .options(selectinload(AutomationRun.automation))
            .where(AutomationRun.id == run_id)
        )
        return result.scalars().one()


async def _reload(session_factory, run_id) -> AutomationRun:
    async with session_factory() as session:
        result = await session.execute(
            select(AutomationRun).where(AutomationRun.id == run_id)
        )
        return result.scalars().one()


def _mock_backend(start_result) -> MagicMock:
    backend = MagicMock()
    backend.is_local_mode = False
    ctx = ExecutionContext(
        agent_url='http://agent.test',
        session_key='sk-1',
        sandbox_id='sbx-1',
        api_url='https://app.test',
        api_key='sk-user',
    )
    backend.get_execution_context = AsyncMock(return_value=ctx)
    backend.release_context = AsyncMock()
    backend.build_env_vars = MagicMock(return_value={})
    backend.get_work_dir = MagicMock(return_value='/workspace/project')
    backend.git_connections = AsyncMock(
        return_value={'github': None, 'gitlab': 'https://gitlab.com'}
    )
    if isinstance(start_result, Exception):
        backend.start_conversation = AsyncMock(side_effect=start_result)
    else:
        backend.start_conversation = AsyncMock(return_value=start_result)
    return backend


async def test_real_dispatcher_persists_failed_on_launch_refusal(
    session_factory, mock_settings, mock_client
):
    """A real inference/launch refusal (``ConversationStartError``) raised
    from the mocked backend's ``start_conversation`` call, propagated
    through the REAL, unmodified ``_execute_run`` caller, must be persisted
    as a terminal FAILED row -- not a stale RUNNING, and no sandbox command
    must ever execute."""
    run = await _make_run(
        session_factory, preset_metadata={'preset_type': 'prompt', 'prompt': 'hi'}
    )
    backend = _mock_backend(
        ConversationStartError(
            'OpenHands API failed to start the conversation: Sandbox not found',
            code='start_task_error',
        )
    )

    with (
        patch('openhands.automation.dispatcher.get_backend', return_value=backend),
        patch('openhands.automation.dispatcher.upload_files', new_callable=AsyncMock),
        patch(
            'openhands.automation.dispatcher.execute_in_context',
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        await _execute_run(run, mock_settings, session_factory, mock_client)

    mock_execute.assert_not_awaited()
    backend.release_context.assert_awaited_once()
    reloaded = await _reload(session_factory, run.id)
    assert reloaded.status == AutomationRunStatus.FAILED
    assert reloaded.bash_command_id is None
    detail = cast(dict, reloaded.status_detail)
    assert detail['operation'] == 'start_conversation'
    assert detail['code'] == 'start_task_error'


async def test_real_dispatcher_does_not_fail_the_run_on_successful_launch(
    session_factory, mock_settings, mock_client
):
    """The mirror positive case: a real ``StartedConversation`` returned by
    the mocked backend must NOT be turned into a failure by the real
    dispatcher caller, and the run must reach command execution (no false
    failure, no run stuck short of its sandbox command)."""
    run = await _make_run(
        session_factory, preset_metadata={'preset_type': 'prompt', 'prompt': 'hi'}
    )
    backend = _mock_backend(
        StartedConversation(
            conversation_id=str(uuid.uuid4()), agent_profile_id=PROFILE_ID
        )
    )

    with (
        patch('openhands.automation.dispatcher.get_backend', return_value=backend),
        patch('openhands.automation.dispatcher.upload_files', new_callable=AsyncMock),
        patch(
            'openhands.automation.dispatcher.execute_in_context',
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        mock_execute.return_value = MagicMock(
            success=True, bash_command_id='cmd-1', error=None
        )
        await _execute_run(run, mock_settings, session_factory, mock_client)

    mock_execute.assert_awaited_once()
    backend.release_context.assert_not_awaited()
    reloaded = await _reload(session_factory, run.id)
    # _execute_run itself does not mark COMPLETED (the entrypoint's own
    # fire_callback does, a later leg not exercised here) -- but it must not
    # have been turned into a failure.
    assert reloaded.status != AutomationRunStatus.FAILED
