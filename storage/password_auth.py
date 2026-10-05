from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.auth.password_auth import utc_now
from storage.base import Base


class PasswordAuthAccount(Base):
    __tablename__ = 'password_auth_account'

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), primary_key=True
    )
    normalized_email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the account row was provisioned by an invitation rather than by
    # the person themselves. Only that invitation may hand out a setup link,
    # so an admin can never set the password of a pre-existing account.
    created_by_org_invitation_id: Mapped[int | None] = mapped_column(
        ForeignKey('org_invitation.id', ondelete='SET NULL'), nullable=True
    )
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default='1'
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class PasswordAuthToken(Base):
    __tablename__ = 'password_auth_token'
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('setup', 'reset')", name='ck_password_auth_token_purpose'
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('password_auth_account.user_id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    org_invitation_id: Mapped[int | None] = mapped_column(
        ForeignKey('org_invitation.id', ondelete='SET NULL'), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    token_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id'), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
