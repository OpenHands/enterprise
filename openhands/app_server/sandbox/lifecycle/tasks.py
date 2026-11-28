"""The worker jobs that pause and delete sandboxes.

Every job is safe to run twice: procrastinate can run a job again when it
wrongly thinks the worker that took it has died.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime

from procrastinate import Blueprint, JobContext
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
)
from openhands.app_server.sandbox import workspace_archive
from openhands.app_server.sandbox.lifecycle.rules import (
    Action,
    Decision,
    decide,
    max_session_due,
)
from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
from openhands.app_server.sandbox.lifecycle.sweep import find_due_sandbox_ids
from openhands.app_server.sandbox.managed_sandbox_service import (
    ManagedSandboxService,
    ManagedSandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_store import (
    LifecycleState,
    StoredSandbox,
    lock_stored_sandbox_if_free,
    mark_paused,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import ADMIN, USER_CONTEXT_ATTR

_logger = logging.getLogger(__name__)

# The most checks one sweep queues. The longest-inactive sandboxes go first,
# and the next sweep, a minute later, picks up the rest.
SWEEP_BATCH_SIZE = 1000
CHECK_TIMEOUT_SECONDS = 120

lifecycle = Blueprint()


def _managed_backend() -> ManagedSandboxServiceInjector | None:
    """The configured backend, when the app manages its sandboxes."""
    from openhands.app_server.config import get_global_config

    injector = get_global_config().sandbox
    if isinstance(injector, ManagedSandboxServiceInjector):
        return injector
    return None


@contextlib.asynccontextmanager
async def _services() -> AsyncIterator[
    tuple[AsyncSession, ManagedSandboxService, AppConversationInfoService]
]:
    """The services the jobs use, acting for every user.

    They share one database session, committed when the block ends. The
    conversation info service is built on that same session so the archive
    step's reads and the row lock see one consistent transaction.
    """
    from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
        SQLAppConversationInfoService,
    )
    from openhands.app_server.config import get_sandbox_service, get_user_context
    from openhands.app_server.services.db_session import get_db_session

    state = InjectorState()
    setattr(state, USER_CONTEXT_ATTR, ADMIN)
    async with (
        get_db_session(state) as db_session,
        get_sandbox_service(state) as sandbox_service,
        get_user_context(state, None) as user_context,
    ):
        # The jobs only run when the backend's injector is managed.
        assert isinstance(sandbox_service, ManagedSandboxService)
        conversation_info_service = SQLAppConversationInfoService(
            db_session=db_session, user_context=user_context
        )
        yield db_session, sandbox_service, conversation_info_service


async def pause_running_sandboxes() -> None:
    """Pause every sandbox recorded as running, at once.

    Each pause has its own transaction, so one failure does not undo the others.
    """
    backend = _managed_backend()
    if backend is None:
        return
    async with _services() as (db_session, _, _):
        result = await db_session.execute(
            select(StoredSandbox.id).where(
                StoredSandbox.backend == backend.backend,
                StoredSandbox.lifecycle_state == LifecycleState.RUNNING,
            )
        )
        sandbox_ids = list(result.scalars())

    async def pause(sandbox_id: str) -> None:
        try:
            async with _services() as (_, sandbox_service, _):
                paused = await sandbox_service.pause_sandbox(sandbox_id)
        except Exception:
            _logger.exception(
                'sandbox_lifecycle.pause_failed', extra={'sandbox_id': sandbox_id}
            )
            return
        if not paused:
            _logger.warning(
                'sandbox_lifecycle.pause_failed', extra={'sandbox_id': sandbox_id}
            )

    await asyncio.gather(*(pause(sandbox_id) for sandbox_id in sandbox_ids))


@lifecycle.periodic(cron='* * * * *')
@lifecycle.task(
    name='sweep', queueing_lock='sandbox_lifecycle:sweep', pass_context=True
)
async def sweep(context: JobContext, timestamp: int) -> None:
    """Queue a check for every sandbox that may be due for a pause or delete."""
    backend = _managed_backend()
    if backend is None:
        return
    async with _services() as (db_session, _, _):
        sandbox_ids = await find_due_sandbox_ids(
            db_session,
            backend.backend,
            backend.lifecycle,
            now=datetime.now(UTC),
            limit=SWEEP_BATCH_SIZE,
        )
    await queue_checks(sandbox_ids)


async def queue_checks(sandbox_ids: Iterable[str]) -> None:
    """Queue a check for each sandbox that has none waiting.

    One at a time, because a batch fails as a whole on one conflict.
    """
    for sandbox_id in sandbox_ids:
        try:
            await check.configure(queueing_lock=f'sandbox:{sandbox_id}').defer_async(
                sandbox_id=sandbox_id
            )
        except AlreadyEnqueued:
            # An earlier sweep queued one, and it has not run yet.
            pass


@lifecycle.task(name='check')
async def check(sandbox_id: str) -> None:
    """Pause or delete one sandbox, if a lifecycle rule says so."""
    backend = _managed_backend()
    if backend is None:
        return
    async with (
        asyncio.timeout(CHECK_TIMEOUT_SECONDS),
        _services() as (db_session, sandbox_service, conversation_info_service),
    ):
        await check_sandbox(
            sandbox_id,
            backend=backend.backend,
            settings=backend.lifecycle,
            db_session=db_session,
            sandbox_service=sandbox_service,
            conversation_info_service=conversation_info_service,
            now=datetime.now(UTC),
        )


async def _archive_conversations_for_sandbox(
    sandbox_service: ManagedSandboxService,
    conversation_info_service: AppConversationInfoService,
    sandbox_id: str,
) -> bool:
    """Archive every conversation workspace tied to the sandbox before it is torn down.

    The idle/expiry reap path pauses or deletes a sandbox without going through
    conversation delete, so on Docker Compose / self-hosted installs the
    workspace would be captured by nobody. This closes that gap by running the
    same conversation-scoped capture the delete finalizer does, for every
    conversation still referencing the sandbox, using the workspace path pinned
    at creation (read from the conversation's tags).

    Returns whether the sandbox is safe to pause or delete. A REQUIRED archive
    that could not confirm a capture returns False so the caller leaves the
    sandbox (and its runtime) for the next sweep to retry — the durability
    backstop. Missing conversations, disabled archiving, or best-effort failures
    return True. Never raises.
    """
    if not workspace_archive.archive_enabled():
        return True
    try:
        page = await conversation_info_service.search_app_conversation_info(
            sandbox_id__eq=sandbox_id, limit=100, include_sub_conversations=True
        )
    except Exception:
        _logger.exception(
            'sandbox_lifecycle.archive_lookup_failed',
            extra={'sandbox_id': sandbox_id},
        )
        # Could not read who to capture: keep the sandbox for a retry rather
        # than risk tearing it down with uncaptured work.
        return not workspace_archive.archive_required()
    if not page.items:
        # No conversation references it (or all were already deleted): nothing
        # to capture, proceed.
        return True
    safe_to_reap = True
    for info in page.items:
        workspace_path = info.tags.get(ARCHIVE_WORKSPACE_PATH_TAG_KEY)
        try:
            archived = await sandbox_service.archive_conversation_workspace(
                sandbox_id,
                conversation_id=info.id.hex,
                workspace_path=workspace_path,
            )
        except Exception:
            _logger.exception(
                'sandbox_lifecycle.archive_conversation_failed',
                extra={'sandbox_id': sandbox_id, 'conversation_id': str(info.id)},
            )
            archived = False
        if not archived:
            safe_to_reap = False
            _logger.warning(
                'sandbox_lifecycle.archive_required_failed',
                extra={'sandbox_id': sandbox_id, 'conversation_id': str(info.id)},
            )
    return safe_to_reap


async def check_sandbox(
    sandbox_id: str,
    *,
    backend: str,
    settings: SandboxLifecycleSettings,
    db_session: AsyncSession,
    sandbox_service: ManagedSandboxService,
    conversation_info_service: AppConversationInfoService,
    now: datetime,
) -> Decision | None:
    """Apply the rules to one sandbox. The caller commits.

    Returns None when the sandbox was left alone because its row is gone, or
    because a pause, resume or delete holds the row right now.
    """
    row = await lock_stored_sandbox_if_free(db_session, backend, sandbox_id)
    if row is None:
        return None

    sandbox = await sandbox_service.get_sandbox(sandbox_id)
    live_status = sandbox.status if sandbox else SandboxStatus.MISSING

    idle_time = None
    if (
        sandbox is not None
        and live_status == SandboxStatus.RUNNING
        and not max_session_due(row, settings, now)
    ):
        idle_time = await sandbox_service.get_idle_time(sandbox)

    decision = decide(row, live_status, idle_time, settings, now)
    log_extra = {
        'sandbox_id': sandbox_id,
        'backend': backend,
        'sandbox_spec_id': row.sandbox_spec_id,
        'reason': decision.reason,
        'idle_time': idle_time,
        'last_active_at': (decision.last_active_at or row.last_active_at).isoformat(),
        'state_changed_at': row.state_changed_at.isoformat(),
    }
    if decision.action == Action.PAUSE:
        # Capture every conversation's workspace before the runtime is stopped,
        # so an idle/max-session pause does not lose work on self-hosted installs
        # where the runtime's own idle reap does not run.
        if not await _archive_conversations_for_sandbox(
            sandbox_service, conversation_info_service, sandbox_id
        ):
            _logger.info(
                'sandbox_lifecycle.pause_deferred_for_archive',
                extra=log_extra,
            )
            return decision
        # pause_sandbox updates this same row, through the session's identity
        # map, and leaves it running when the provider skips the pause.
        if not await sandbox_service.pause_sandbox(sandbox_id):
            _logger.warning('sandbox_lifecycle.pause_failed', extra=log_extra)
        elif row.lifecycle_state == LifecycleState.PAUSED:
            _logger.info('sandbox_lifecycle.paused', extra=log_extra)
        else:
            _logger.info('sandbox_lifecycle.pause_skipped', extra=log_extra)
    elif decision.action == Action.DELETE:
        # The last step before teardown: capture every conversation's workspace
        # so the delete does not lose work. A REQUIRED failure keeps the sandbox
        # + runtime for the next sweep to retry, instead of tearing it down.
        if not await _archive_conversations_for_sandbox(
            sandbox_service, conversation_info_service, sandbox_id
        ):
            _logger.info(
                'sandbox_lifecycle.delete_deferred_for_archive',
                extra=log_extra,
            )
            return decision
        await sandbox_service.delete_sandbox(sandbox_id)
        _logger.info('sandbox_lifecycle.deleted', extra=log_extra)
    elif decision.action == Action.RECORD_PAUSE:
        mark_paused(row)
        _logger.info('sandbox_lifecycle.found_paused', extra=log_extra)
    elif decision.action == Action.RECORD_ACTIVITY:
        assert decision.last_active_at is not None
        row.last_active_at = decision.last_active_at
    return decision
