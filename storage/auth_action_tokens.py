"""Purpose-bound, single-use account action tokens."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.auth_types import AuthDateTime
from storage.base import Base


class AuthActionToken(Base):
    __tablename__ = 'auth_action_tokens'

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), index=True
    )
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        AuthDateTime(), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(AuthDateTime(), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(AuthDateTime())

    __table_args__ = (
        CheckConstraint('length(token_digest) = 64', name='ck_auth_action_digest'),
        CheckConstraint('expires_at > created_at', name='ck_auth_action_expiry'),
        CheckConstraint(
            "purpose IN ('password_reset', 'email_verification', 'invitation')",
            name='ck_auth_action_purpose',
        ),
    )
