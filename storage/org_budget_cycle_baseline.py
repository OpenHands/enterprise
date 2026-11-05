"""SQLAlchemy model for per-member budget cycle baselines."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class OrgBudgetCycleBaseline(Base):
    """A member's cumulative LiteLLM spend at the start of one budget cycle.

    Per-member caps are ``baseline_spend + allowance``. One row per
    (organization, cycle, member) records where the value came from and when
    it was observed, so caps stay auditable across cycles and repairs. It
    supersedes the ``org_budget_settings.user_cycle_start_spend`` JSON map,
    which is still dual-written during the compatibility window.
    """

    __tablename__ = 'org_budget_cycle_baseline'
    __table_args__ = (
        UniqueConstraint(
            'org_id',
            'cycle_start_at',
            'user_id',
            name='uq_org_budget_cycle_baseline_member_cycle',
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), nullable=False
    )
    # LiteLLM team member id. Not a foreign key: LiteLLM-only identities
    # (service accounts) carry baselines but have no ``user`` row.
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    cycle_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    baseline_spend: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Reserved for generation-based reconciliation (OHE-3259); unset until then.
    recovery_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    # Provenance of the baseline value.
    SOURCE_LIVE_ROLLOVER = 'live_rollover'
    SOURCE_ENABLEMENT = 'enablement'
    SOURCE_UPGRADE_RECOVERY = 'upgrade_recovery'
    SOURCE_IMPORTED = 'imported'
    # A member's first sync after joining the LiteLLM team.
    SOURCE_MEMBER_ADDED = 'member_added'
