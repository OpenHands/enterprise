"""Revocable browser sessions containing only SHA-256 token digests."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.auth_types import AuthDateTime
from storage.base import Base


class AuthSession(Base):
    __tablename__ = 'auth_sessions'

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        AuthDateTime(), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(AuthDateTime(), index=True)
    restricted: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        CheckConstraint('length(token_digest) = 64', name='ck_auth_session_digest'),
        CheckConstraint('expires_at > created_at', name='ck_auth_session_expiry'),
    )
