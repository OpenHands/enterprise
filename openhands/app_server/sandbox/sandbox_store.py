"""App-owned record of the sandboxes the docker and E2B backends create.

Both backends used to read ownership back out of the provider — container
labels for docker, sandbox metadata for E2B. Neither is a sound place for it.
The value drives an authorization decision: ``session_auth.validate_session_key``
reads ``created_by_user_id`` off the sandbox and ``sandbox_router`` uses it to
pick whose secrets, provider tokens and unmasked ``llm_api_key`` to release. No
provider surveyed offers metadata that is both mutable and durable — E2B's is
immutable and disappears with the sandbox, and Daytona documents that any org
API key reaches any sandbox whatever its labels.

Provider labels are still written. They are the tag a reconciler needs to find
a sandbox the app lost track of, not the ownership record.

``RemoteSandboxService`` keeps its own ``v1_remote_sandbox`` table; moving it
onto this one is a separate change.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import Select, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.sql_utils import Base, StoredSecretStr, UtcDateTime

DOCKER_BACKEND = 'docker'
E2B_BACKEND = 'e2b'


def hash_session_api_key(session_api_key: str) -> str:
    """Hash a session API key using SHA-256."""
    return hashlib.sha256(session_api_key.encode()).hexdigest()


class StoredSandbox(Base):
    """A sandbox the app created, and who it belongs to.

    Shared by the docker and E2B backends. ``backend`` keeps their rows apart
    so a deployment that switches ``RUNTIME`` does not read one backend's
    sandboxes through the other's provider.

    Rows are soft deleted: ``delete_sandbox`` stamps ``deleted_at`` rather than
    removing the row, so the history survives for a later lifecycle or usage
    feature. Every read path filters on ``deleted_at IS NULL``.

    ``session_api_key`` is the key itself, encrypted at rest, and only E2B
    writes it. The hash beside it answers "which sandbox holds this key" on an
    index, which is all the webhook path needs; the key itself has to be
    handed back out of ``get_sandbox`` because every call the app makes to an
    agent server carries it. Docker recovers it from the container's
    environment and the runtime API returns it on every read, so for those two
    backends there is nothing to keep.
    """

    __tablename__ = 'v1_sandbox'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    backend: Mapped[str] = mapped_column(String, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    sandbox_spec_id: Mapped[str] = mapped_column(String, index=True)
    session_api_key_hash: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    session_api_key: Mapped[SecretStr | None] = mapped_column(
        StoredSecretStr, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True, index=True
    )


@dataclass
class StoredSandboxPage:
    """One page of stored sandboxes, newest first."""

    items: list[StoredSandbox]
    next_page_id: str | None


async def secure_select(
    user_context: UserContext, backend: str
) -> Select[tuple[StoredSandbox]]:
    """Select over the live sandboxes the caller may see.

    A caller with a user id sees only their own rows. A caller without one —
    OSS single user mode, and the ADMIN contexts webhook and session key auth
    run under — sees every row. That second case is what
    ``session_auth.validate_session_key`` and ``webhook_router.valid_sandbox``
    are built on, so narrowing it breaks authentication.
    """
    query = select(StoredSandbox).where(
        StoredSandbox.backend == backend,
        StoredSandbox.deleted_at.is_(None),
    )
    user_id = await user_context.get_user_id()
    if user_id:
        query = query.where(StoredSandbox.created_by_user_id == user_id)
    return query


async def get_stored_sandbox(
    db_session: AsyncSession,
    user_context: UserContext,
    backend: str,
    sandbox_id: str,
) -> StoredSandbox | None:
    """Get a sandbox by id, or None when the caller may not see it."""
    stmt = await secure_select(user_context, backend)
    stmt = stmt.where(StoredSandbox.id == sandbox_id)
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


async def get_stored_sandbox_by_session_api_key(
    db_session: AsyncSession,
    user_context: UserContext,
    backend: str,
    session_api_key: str,
) -> StoredSandbox | None:
    """Get a sandbox by session API key, on the hash index."""
    stmt = await secure_select(user_context, backend)
    stmt = stmt.where(
        StoredSandbox.session_api_key_hash == hash_session_api_key(session_api_key)
    )
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


async def search_stored_sandboxes(
    db_session: AsyncSession,
    user_context: UserContext,
    backend: str,
    page_id: str | None,
    limit: int,
) -> StoredSandboxPage:
    """Page over the caller's sandboxes, newest first.

    ``page_id`` is an offset, matching ``RemoteSandboxService``. One extra row
    is read to decide whether there is a next page.
    """
    try:
        offset = int(page_id) if page_id is not None else 0
    except ValueError:
        offset = 0

    stmt = await secure_select(user_context, backend)
    stmt = (
        stmt.order_by(StoredSandbox.created_at.desc()).offset(offset).limit(limit + 1)
    )
    result = await db_session.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    return StoredSandboxPage(
        items=rows,
        next_page_id=str(offset + limit) if has_more else None,
    )
