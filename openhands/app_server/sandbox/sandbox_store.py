"""App-owned record of the sandboxes every backend creates.

Ownership lives here because it drives an authorization decision:
``session_auth.validate_session_key`` reads ``created_by_user_id`` off the
sandbox, and ``sandbox_router`` uses it to pick whose secrets, provider tokens
and unmasked ``llm_api_key`` to release.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import Select, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openhands.app_server.errors import AuthError
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.sql_utils import Base, StoredSecretStr, UtcDateTime

REMOTE_BACKEND = 'remote'
DOCKER_BACKEND = 'docker'
E2B_BACKEND = 'e2b'
K8S_AGENT_SANDBOX_BACKEND = 'k8s-agent-sandbox'


def hash_session_api_key(session_api_key: str) -> str:
    """Hash a session API key using SHA-256."""
    return hashlib.sha256(session_api_key.encode()).hexdigest()


class StoredSandbox(Base):
    """A sandbox the app created, and who it belongs to.

    Every backend shares this table. It keeps the name ``v1_remote_sandbox``
    because renaming a live table breaks app servers still running the
    previous release during a deploy.

    ``backend`` keeps each backend's rows apart, so a deployment that switches
    ``RUNTIME`` does not read one backend's rows through another's provider.
    Rows written without one are the remote backend's.

    ``session_api_key`` is the key itself, encrypted at rest, for backends
    that cannot read a key back from the provider. The hash serves the indexed
    lookup on the webhook path.
    """

    __tablename__ = 'v1_remote_sandbox'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    backend: Mapped[str] = mapped_column(String, server_default=REMOTE_BACKEND)
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


@dataclass
class StoredSandboxPage:
    """One page of stored sandboxes, newest first."""

    items: list[StoredSandbox]
    next_page_id: str | None


async def require_user_id(user_context: UserContext) -> str:
    """Return the caller's user id, or raise ``AuthError`` when there is none."""
    user_id = await user_context.get_user_id()
    if not user_id:
        raise AuthError('User authentication required')
    return user_id


async def secure_select(
    user_context: UserContext, backend: str
) -> Select[tuple[StoredSandbox]]:
    """Select over one backend's sandboxes that the caller may see.

    A user sees only their own rows. ``ADMIN`` sees every row, because session
    key auth, webhooks and the integration callbacks read sandboxes across
    users. Any other caller without a user id is refused.
    """
    query = select(StoredSandbox).where(StoredSandbox.backend == backend)
    if user_context == ADMIN:
        return query
    user_id = await require_user_id(user_context)
    return query.where(StoredSandbox.created_by_user_id == user_id)


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

    ``page_id`` is an offset. One extra row is read to decide whether there is
    a next page.
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
