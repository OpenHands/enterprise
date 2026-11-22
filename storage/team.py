"""SQLAlchemy model for a team inside an organization.

A team is a tenant-scoped grouping that owns the settings layer between the
organization and the individual member. Its identity is always the explicit
``(org_id, id)`` pair: no lookup may rely on an ambient tenant or on a globally
unique team id, which is why the composite unique constraint below also exists
for the ``team_member`` foreign key.

The team row itself owns the scope document and its revision. There is no
second table and no duplicated projection: the document is a sparse local
contribution, never a copy of the resolved org/member layers.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.settings_document import empty_settings_document


class Team(Base):
    """A named team owned by exactly one organization."""

    __tablename__ = 'team'
    __table_args__ = (
        UniqueConstraint('org_id', 'name', name='uq_team_org_id_name'),
        # Redundant with the primary key alone, but PostgreSQL requires a
        # unique constraint on exactly these columns to be the target of the
        # composite (org_id, team_id) foreign key from team_member.
        UniqueConstraint('org_id', 'id', name='uq_team_org_id_id'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # The single settings document owned by this team scope.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=empty_settings_document
    )
    # Optimistic concurrency for settings edits; unrelated column updates do
    # not bump it.
    revision: Mapped[int] = mapped_column(nullable=False, default=0, server_default='0')
    # Last settings editor, kept for the admin/API response.
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey('user.id', ondelete='SET NULL'), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey('user.id', ondelete='SET NULL'), nullable=True
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
