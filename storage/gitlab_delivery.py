from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class GitlabDelivery(Base):
    """A GitLab webhook delivery the event handler has accepted, by body hash."""

    __tablename__ = 'gitlab_delivery'
    __table_args__ = (Index('ix_gitlab_delivery_received_at', 'received_at'),)

    body_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text('now()')
    )
