"""Find the sandboxes that may be due for a pause or a delete.

The sweep reads only the sandbox table, so it costs no provider calls. A row it
returns is only a candidate: the check reads the live status and the agent's
idle time before it acts. A sandbox in active use comes back about once per
idle period, when the activity recorded on its row gets older than that.
"""

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings
from openhands.app_server.sandbox.sandbox_store import LifecycleState, StoredSandbox
from openhands.app_server.utils.sql_utils import UtcDateTime

# When the sandbox last started, resumed or paused, or its agent was last seen
# working, whichever is latest.
LAST_RAN_AT = func.greatest(
    StoredSandbox.state_changed_at,
    StoredSandbox.last_active_at,
    type_=UtcDateTime(),
)


def _due(settings: SandboxLifecycleSettings, now: datetime) -> ColumnElement[bool]:
    running = StoredSandbox.lifecycle_state == LifecycleState.RUNNING
    rules: list[ColumnElement[bool]] = []
    if settings.idle_seconds:
        cutoff = now - timedelta(seconds=settings.idle_seconds)
        rules.append(and_(running, LAST_RAN_AT <= cutoff))
    if settings.max_session_seconds:
        cutoff = now - timedelta(seconds=settings.max_session_seconds)
        rules.append(and_(running, StoredSandbox.state_changed_at <= cutoff))
    if settings.delete_after_seconds:
        cutoff = now - timedelta(seconds=settings.delete_after_seconds)
        rules.append(LAST_RAN_AT <= cutoff)
    return or_(false(), *rules)


async def find_due_sandbox_ids(
    db_session: AsyncSession,
    backend: str,
    settings: SandboxLifecycleSettings,
    now: datetime,
    limit: int,
) -> list[str]:
    """One backend's sandboxes that may be due, longest inactive first."""
    stmt = (
        select(StoredSandbox.id)
        .where(StoredSandbox.backend == backend, _due(settings, now))
        .order_by(LAST_RAN_AT)
        .limit(limit)
    )
    result = await db_session.execute(stmt)
    return list(result.scalars())
