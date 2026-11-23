"""Real callback/persistence lifecycle tests for the automation-reference
source (openhands/automation/utils/run.py, models.py), NOT a reimplementation.

Scope and what's mocked/stubbed (see also PROGRESS.md / handoff v2 FINDINGS):
  - DB: an in-memory SQLite engine running the REAL SQLAlchemy ORM models
    (``AutomationRun``, ``Automation``) and the REAL ``mark_run_status`` /
    ``mark_run_terminal`` functions from ``openhands.automation.utils.run``.
    No Postgres container is available in this sandbox; SQLite is a stand-in
    database engine, not a mock of the status-marking logic itself, which
    runs unmodified.
  - Network/sandbox/LLM: not exercised at all here -- this file tests only
    the callback/dispatcher-side PERSISTENCE contract (the terminal status
    write), which is the explicit Task 2 ask: "assert FAILED terminal
    stored status/error for an inference/launch refusal, successful case
    completion, no false success or stale RUNNING."
  - Cross-repo dependency: ``openhands.automation.utils.run`` transitively
    imports ``openhands.automation.schemas`` (via ``git_sync``), which
    imports ``from openhands.workspace import RepoSource`` -- a real
    OpenHands Cloud client package, confirmed (per root's isolated
    one-off-container image check) to be ``openhands-workspace==1.62.0`` in
    the actual deployed automation image alongside
    ``openhands-automation==1.32.1`` and ``openhands-sdk==1.62.0``. That
    exact package is installed here with ``--no-deps`` (no SDK version
    change) so this real code imports without any shim or reimplementation.

NOT established by this file: the dispatcher's full `_execute_run` control
flow (sandbox provisioning, `CloudSandboxBackend.start_conversation`, the
entrypoint script's own `fire_callback` HTTP call) is NOT exercised -- only
the DB-side terminal-status write every one of those failure/success paths
ultimately calls through `_fail()` / `mark_run_terminal()`. No live model
provider or sandbox execution is claimed.
"""

from __future__ import annotations  # noqa: I001

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Extends the ``openhands`` namespace package with AUTOMATION_SOURCE_DIR's
# ``openhands/automation`` submodule (see _backend_adapter.py's own
# docstring for why this can't just be a sys.path insert); must run, and
# must be imported, before the openhands.automation imports below -- keep
# this import block separate (ruff's import-sort would otherwise merge and
# reorder it ahead of this comment, breaking that ordering requirement).
from . import _backend_adapter as _ba  # noqa: E402,F401

from openhands.automation.models import (  # noqa: E402
    Automation,
    AutomationRun,
    AutomationRunStatus,
    Base,
)
from openhands.automation.utils.run import mark_run_status, mark_run_terminal  # noqa: E402


@pytest.fixture
async def session_factory():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_running_run(session_factory) -> AutomationRun:
    """A real PENDING->RUNNING run row, persisted through the real ORM."""
    owner_id = uuid.uuid4()
    async with session_factory() as session:
        automation = Automation(
            id=uuid.uuid4(),
            user_id=owner_id,
            created_by_user_id=owner_id,
            org_id=uuid.uuid4(),
            name='test-automation',
            trigger={'type': 'cron', 'schedule': '0 * * * *'},
            tarball_path='s3://bucket/tarball.tar.gz',
            entrypoint='python3 main.py',
        )
        session.add(automation)
        await session.flush()
        run = AutomationRun(
            id=uuid.uuid4(),
            automation_id=automation.id,
            status=AutomationRunStatus.PENDING,
        )
        session.add(run)
        await session.commit()
        await mark_run_status(session, run, AutomationRunStatus.RUNNING)
        await session.commit()
        return run


async def test_failed_launch_refusal_persists_failed_with_error(session_factory):
    """An inference/launch refusal (e.g. the customer-gateway's
    CustomerGatewayLaunchError str, or a ConversationStartError) must leave
    the run FAILED with its error recorded -- not stale RUNNING, not a false
    COMPLETED."""
    run = await _make_running_run(session_factory)
    await mark_run_terminal(
        session_factory,
        run,
        AutomationRunStatus.FAILED,
        'customer gateway access denied: not_a_member',
    )

    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(AutomationRun).where(AutomationRun.id == run.id)
        )
        db_run = result.scalars().first()

    assert db_run.status == AutomationRunStatus.FAILED
    assert db_run.error_detail == 'customer gateway access denied: not_a_member'
    assert db_run.completed_at is not None
    assert db_run.status != AutomationRunStatus.RUNNING


async def test_successful_run_persists_completed_not_failed(session_factory):
    run = await _make_running_run(session_factory)
    await mark_run_terminal(session_factory, run, AutomationRunStatus.COMPLETED)

    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(AutomationRun).where(AutomationRun.id == run.id)
        )
        db_run = result.scalars().first()

    assert db_run.status == AutomationRunStatus.COMPLETED
    assert db_run.error_detail is None
    assert db_run.completed_at is not None


async def test_mark_run_terminal_does_not_overwrite_already_terminal_run(
    session_factory,
):
    """mark_run_terminal's own race guard: only transitions a run that is
    STILL RUNNING. A run already marked COMPLETED (e.g. by a prior/duplicate
    callback) must not be flipped to FAILED by a later, stale failure
    signal -- this is the real 'no false success / no stale overwrite'
    safety net, exercised against the real DB-backed function, not asserted
    by inspection of the source alone."""
    run = await _make_running_run(session_factory)
    await mark_run_terminal(session_factory, run, AutomationRunStatus.COMPLETED)

    # A second, stale FAILED signal arrives after the run already completed.
    await mark_run_terminal(session_factory, run, AutomationRunStatus.FAILED, 'stale')

    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(AutomationRun).where(AutomationRun.id == run.id)
        )
        db_run = result.scalars().first()

    # Still COMPLETED: the guard refused the later stale FAILED transition.
    assert db_run.status == AutomationRunStatus.COMPLETED
    assert db_run.error_detail is None


async def test_run_never_left_running_after_terminal_call(session_factory):
    """Belt-and-suspenders for the 'no stale RUNNING' requirement: after any
    terminal call, status is never RUNNING, regardless of which terminal
    status was requested."""
    for status in (AutomationRunStatus.FAILED, AutomationRunStatus.COMPLETED):
        run = await _make_running_run(session_factory)
        await mark_run_terminal(
            session_factory, run, status, 'boom' if status.name == 'FAILED' else None
        )
        async with session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(AutomationRun).where(AutomationRun.id == run.id)
            )
            db_run = result.scalars().first()
        assert db_run.status != AutomationRunStatus.RUNNING
        assert db_run.status == status
