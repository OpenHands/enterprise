"""Agent Canvas Apps approved for an organization, and who enabled them (OHE-3773).

An approved app is stored as the manifest and entrypoint of the exact commit
that was fetched, so members only ever load that code. A member row means that
member has the app enabled; removing the app removes those rows with it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class StoredOrgCanvasApp(Base):
    __tablename__ = 'org_canvas_app'

    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), primary_key=True
    )
    name: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    requested_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_ref: Mapped[str] = mapped_column(String, nullable=False)
    repo_path: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # The entrypoint file. Deferred so listing the apps does not load it.
    bundle: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class StoredOrgMemberCanvasApp(Base):
    __tablename__ = 'org_member_canvas_app'
    __table_args__ = (
        ForeignKeyConstraint(
            ['org_id', 'name'],
            ['org_canvas_app.org_id', 'org_canvas_app.name'],
            ondelete='CASCADE',
        ),
        ForeignKeyConstraint(
            ['org_id', 'user_id'],
            ['org_member.org_id', 'org_member.user_id'],
            ondelete='CASCADE',
        ),
    )

    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)
