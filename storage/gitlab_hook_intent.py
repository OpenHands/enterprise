from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class HookIntentState(StrEnum):
    # Recorded before the create; the hook doesn't carry the row's identity yet.
    OPEN = 'open'
    # The row's current identity. At most one per row.
    COMMITTED = 'committed'
    # Was committed, then a later intent became current.
    REPLACED = 'replaced'
    # Will never become current, so its hook can be deleted.
    ABANDONED = 'abandoned'


class GitlabHookIntent(Base):
    """One attempt to give a GitLab hook an identity, named by the uuid in its URL."""

    __tablename__ = 'gitlab_hook_intent'
    __table_args__ = (
        CheckConstraint(
            "state IN ('open', 'committed', 'replaced', 'abandoned')",
            name='ck_gitlab_hook_intent_state',
        ),
        Index('ix_gitlab_hook_intent_webhook_id', 'webhook_id'),
        Index(
            'uq_gitlab_hook_intent_committed',
            'webhook_id',
            unique=True,
            postgresql_where=text("state = 'committed'"),
        ),
    )

    uuid: Mapped[str] = mapped_column(String, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('gitlab_webhook.id', ondelete='CASCADE'), nullable=False
    )
    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text('now()')
    )
