"""SQLAlchemy models for worklogs: named, append-only logs scoped to a factory."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class Worklog(Base):
    """A worklog of one factory.

    ``factory_id`` has no foreign key: factories live in the automation
    service's database. ``slug`` is the identifier in URLs and never changes.
    """

    __tablename__ = 'worklogs'
    __table_args__ = (
        UniqueConstraint(
            'org_id', 'factory_id', 'slug', name='uq_worklogs_factory_slug'
        ),
        UniqueConstraint(
            'org_id', 'factory_id', 'name', name='uq_worklogs_factory_name'
        ),
        Index(
            'uq_worklogs_one_default_per_factory',
            'org_id',
            'factory_id',
            unique=True,
            postgresql_where=text('is_default'),
        ),
        Index('ix_worklogs_factory_id', 'factory_id'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), nullable=False
    )
    factory_id: Mapped[UUID] = mapped_column(nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default='false'
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorklogEntry(Base):
    """One entry. Append-only: nothing updates or deletes an entry.

    ``body`` is opaque, stored as received and never parsed on read.
    ``author_*`` is set by the server from the credential that wrote it.
    """

    __tablename__ = 'worklog_entries'
    __table_args__ = (
        Index('ix_worklog_entries_worklog_created', 'worklog_id', 'created_at', 'id'),
        Index(
            'ix_worklog_entries_user_author',
            'author_id',
            postgresql_where=text("author_kind = 'user'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    worklog_id: Mapped[UUID] = mapped_column(
        ForeignKey('worklogs.id', ondelete='CASCADE'), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    content_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    author_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
