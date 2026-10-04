"""SQLAlchemy model for instance-wide settings set by a Super Admin."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class InstanceSettings(Base):
    """The company name and logo shown across the instance.

    This table holds at most one row (enforced by a database constraint).
    """

    __tablename__ = 'instance_settings'
    __table_args__ = (CheckConstraint('id = 1', name='single_instance_settings_row'),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A ``data:image/...;base64,...`` URL, already resized by the client.
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
