"""Daily conversation quota accounting models."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class DailyConversationUsage(Base):
    """Atomic per-user conversation-start count for one UTC calendar day."""

    __tablename__ = 'daily_conversation_usage'
    __table_args__ = (UniqueConstraint('user_id', 'usage_date'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('user.id'), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
