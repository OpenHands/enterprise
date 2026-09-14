from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DECIMAL,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from storage.base import Base

if TYPE_CHECKING:
    from storage.org import Org


class BillingSession(Base):
    """
    Represents a Stripe billing session for credit purchases.
    Tracks the status of payment transactions and associated user information.
    """

    __tablename__ = 'billing_sessions'
    __table_args__ = (
        CheckConstraint(
            "credit_target IS NULL OR (credit_target >= 0 AND credit_target < 'Infinity'::float AND org_id IS NOT NULL AND status IN ('in_progress', 'completed'))",
            name='ck_billing_credit_target',
        ),
        Index(
            'uq_pending_credit_org',
            'org_id',
            unique=True,
            postgresql_where=text(
                "status = 'in_progress' AND credit_target IS NOT NULL"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    org_id: Mapped[UUID | None] = mapped_column(ForeignKey('org.id'), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(
            'in_progress',
            'completed',
            'cancelled',
            'error',
            name='billing_session_status_enum',
        ),
        default='in_progress',
    )
    price: Mapped[Decimal] = mapped_column(DECIMAL(19, 4), nullable=False)
    price_code: Mapped[str] = mapped_column(String, nullable=False)
    credit_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    credit_budget_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Relationships
    org: Mapped['Org | None'] = relationship('Org', back_populates='billing_sessions')
