"""SQLAlchemy model for QuotaIncreaseRequest."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from storage.base import Base

if TYPE_CHECKING:
    from storage.user import User


class QuotaIncreaseRequest(Base):
    """A user's request to increase their daily conversation quota.

    Created when a user submits a work email; approved either
    self-service (via signed email verification link) or by an admin.
    """

    __tablename__ = 'quota_increase_request'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id'), nullable=False, index=True
    )
    work_email: Mapped[str] = mapped_column(String(255), nullable=False)
    baseline_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="'pending'"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('user.id'), nullable=True
    )

    # Status constants
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    # Pending requests whose verification token TTL elapsed are expired when
    # the user submits a replacement request.
    STATUS_EXPIRED = 'expired'

    user: Mapped['User'] = relationship('User', foreign_keys=[user_id])
