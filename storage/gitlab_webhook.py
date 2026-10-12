from datetime import datetime
from enum import IntEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    DateTime,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class WebhookStatus(IntEnum):
    PENDING = 0  # Conditions for installation webhook need checking
    VERIFIED = 1  # Conditions are met for installing webhook
    RATE_LIMITED = 2  # API was rate limited, failed to check
    INVALID = 3  # Unexpected error occur when checking (keycloak connection, etc)


class GitlabWebhook(Base):
    """
    Represents a Gitlab webhook configuration for a repository or group.
    """

    __tablename__ = 'gitlab_webhook'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    webhook_exists: Mapped[bool] = mapped_column(nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(String, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    webhook_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    scopes: Mapped[Any] = mapped_column(ARRAY(Text), nullable=True)
    last_synced: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text('CURRENT_TIMESTAMP'),
        onupdate=text('CURRENT_TIMESTAMP'),
        nullable=True,
    )
    claim_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A row needs a reinstall while the request is ahead of the generation the
    # installer last served.
    reinstall_requested_gen: Mapped[int] = mapped_column(
        default=0, server_default='0', nullable=False
    )
    reinstall_done_gen: Mapped[int] = mapped_column(
        default=0, server_default='0', nullable=False
    )

    def __repr__(self) -> str:
        return (
            f'<GitlabWebhook(id={self.id}, group_id={self.group_id}, '
            f'project_id={self.project_id}, last_synced={self.last_synced})>'
        )
