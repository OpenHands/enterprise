"""Database-driven feature flag models.

Mirrors the rule-based pattern of ``user_authorizations``: a flag row holds the
global on/off switch and metadata, while ``FeatureFlagRule`` rows encode
targeting (include/exclude) by user_id, org_id, email pattern, and percentage
rollout. Evaluation precedence is implemented in ``FeatureFlagService``.

When a targeting field is NULL it matches all values of that dimension, so a
rule with every field NULL is a blanket include/exclude.
"""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import DateTime, Float, ForeignKey, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from storage.base import Base


class FeatureFlagRuleEffect(str, Enum):
    """Whether a rule turns the flag on or off for matched targets."""

    INCLUDE = "include"
    EXCLUDE = "exclude"


class FeatureFlag(Base):
    """A single feature flag.

    ``enabled`` is the global on/off switch. When False the flag is off for
    everyone regardless of rules. When True, ``FeatureFlagRule`` rows refine
    who actually receives the flag (include/exclude + percentage rollout).
    """

    __tablename__ = "feature_flags"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
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


class FeatureFlagRule(Base):
    """A targeting rule for a feature flag.

    All match fields are optional; NULL means "matches any value" for that
    dimension. A rule matches a given context only when *every* populated
    dimension matches. ``effect`` then includes or excludes matched contexts.

    ``percentage`` (0-100, inclusive) enables deterministic rollout: a context
    is in the bucket when ``hash(flag_key + user_id) % 100 < percentage``. A
    NULL ``user_id`` cannot participate in percentage rollout and always falls
    back to "in bucket" (i.e. the percentage constraint is skipped).
    """

    __tablename__ = "feature_flag_rules"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    flag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("feature_flags.id", ondelete="CASCADE"),
        nullable=False,
    )
    effect: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    org_id: Mapped[str | None] = mapped_column(String, nullable=True)
    email_pattern: Mapped[str | None] = mapped_column(String, nullable=True)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
