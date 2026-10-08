"""SQLAlchemy model for instance-wide settings set by a Super Admin."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class InstanceSettings(Base):
    """The company name and logo shown across the instance, and first-install state.

    This table holds at most one row (enforced by a database constraint).
    """

    __tablename__ = 'instance_settings'
    __table_args__ = (CheckConstraint('id = 1', name='single_instance_settings_row'),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A ``data:image/...;base64,...`` URL, already resized by the client.
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The first Super Admin, who runs the first-install wizard and owns the
    # setup guide. Set when the first user is created with ``ENABLE_SUPER_ADMIN``
    # on; NULL on instances that already had users. No foreign key: it is
    # written in the same transaction, before the user row exists.
    setup_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    wizard_completed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default='false'
    )
    # The organization the setup guide belongs to.
    guide_org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('org.id', ondelete='SET NULL'), nullable=True
    )
    guide_dismissed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default='false'
    )
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
