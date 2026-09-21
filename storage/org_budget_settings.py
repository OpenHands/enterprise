from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class OrgBudgetSettings(Base):
    __tablename__ = 'org_budget_settings'
    __table_args__ = (
        CheckConstraint(
            "control_mode IN ('managed', 'external', 'needs_adoption')",
            name='ck_org_budget_control_mode',
        ),
        CheckConstraint('control_generation >= 0', name='ck_org_budget_generation'),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id'), nullable=False, unique=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    control_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default='external', server_default='external'
    )
    control_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default='0'
    )
    control_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    control_changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    cycle_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cycle_allowance: Mapped[float | None] = mapped_column(Float, nullable=True)
    cycle_default_user_allowance: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    cycle_user_allowances: Mapped[dict[str, float | None] | None] = mapped_column(
        JSON, nullable=True
    )
    monthly_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    reset_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    default_user_monthly_limit: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    slack_channel: Mapped[str | None] = mapped_column(String, nullable=True)
    slack_team_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cycle_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    cycle_start_spend: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    user_cycle_start_spend: Mapped[dict[str, float]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    litellm_last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    litellm_last_sync_status: Mapped[str | None] = mapped_column(String, nullable=True)
    litellm_last_sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    litellm_last_spend_snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    litellm_last_team_spend: Mapped[float | None] = mapped_column(Float, nullable=True)
    litellm_last_member_spend: Mapped[dict[str, float]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    litellm_known_member_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
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
