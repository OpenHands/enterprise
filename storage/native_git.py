"""Independent native Git credentials; legacy broker tokens remain unchanged."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.native_auth import now_utc


class GitConnection(Base):
    __tablename__ = 'native_git_connection'
    __table_args__ = (
        UniqueConstraint(
            'account_id', 'provider', name='uq_native_git_account_provider'
        ),
        Index(
            'uq_native_git_subject',
            'provider',
            'host',
            'subject',
            unique=True,
            postgresql_where=text('revoked_at IS NULL AND subject IS NOT NULL'),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey('auth_account.id'), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    login: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(String(16))
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    encrypted_email: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(default=0)
    identity_verified: Mapped[bool] = mapped_column(default=False)
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitOAuthState(Base):
    __tablename__ = 'native_git_oauth_state'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey('auth_account.id'), index=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey('browser_session.id', ondelete='CASCADE')
    )
    provider: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(255))
    connection_generation: Mapped[int] = mapped_column()
    encrypted_verifier: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
