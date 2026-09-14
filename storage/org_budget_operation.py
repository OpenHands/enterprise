"""Durable, immutable intent for a single organization budget change."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class OrgBudgetOperation(Base):
    __tablename__ = 'org_budget_operation'
    __table_args__ = (
        UniqueConstraint('org_id', 'idempotency_key', name='uq_budget_operation_key'),
        UniqueConstraint('org_id', 'generation', name='uq_budget_operation_generation'),
        CheckConstraint(
            "status IN ('pending', 'applied', 'abandoned')",
            name='ck_budget_operation_status',
        ),
        CheckConstraint(
            "kind IN ('adopt', 'settings', 'rollover', 'repair')",
            name='ck_budget_operation_kind',
        ),
        CheckConstraint('generation > 0', name='ck_budget_operation_generation'),
        Index(
            'uq_budget_operation_pending',
            'org_id',
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='pending')
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
