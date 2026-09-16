"""Native credentials outlive the disposable application User profile."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTableUUID
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class AuthAccount(Base):
    __tablename__ = 'auth_account'
    __table_args__ = (
        CheckConstraint(
            "state IN ('profile_present','reonboardable','profile_absent_blocked','deleted')",
            name='ck_auth_account_state',
        ),
        CheckConstraint(
            "state = 'deleted' OR (normalized_email IS NOT NULL AND display_email IS NOT NULL)",
            name='ck_auth_account_email',
        ),
        Index(
            'uq_auth_account_live_email',
            'normalized_email',
            unique=True,
            postgresql_where=text("state <> 'deleted'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Only old deleted tombstones may lack contact data. Credentials never own
    # account admission/profile metadata, and IdP email cannot replace it.
    normalized_email: Mapped[str | None] = mapped_column(String(320))
    display_email: Mapped[str | None] = mapped_column(String(320))
    state: Mapped[str] = mapped_column(String(32), default='profile_present')
    session_version: Mapped[int] = mapped_column(default=1)
    # Managed key creation happens after commit; never store a pretend LLM key.
    provisioning_status: Mapped[str] = mapped_column(String(16), default='pending')
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class PasswordCredential(Base):
    __tablename__ = 'password_credential'

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey('auth_account.id'), primary_key=True
    )
    normalized_login_email: Mapped[str] = mapped_column(String(320), unique=True)
    display_email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )


class BrowserSession(SQLAlchemyBaseAccessTokenTableUUID, Base):
    """Standard database token, plus an ID for integration-link correlation."""

    __tablename__ = 'browser_session'

    id: Mapped[UUID] = mapped_column(default=uuid4, unique=True)


class AuthInstallation(Base):
    __tablename__ = 'auth_installation'
    __table_args__ = (
        CheckConstraint('id = 1', name='ck_auth_installation_singleton'),
        CheckConstraint(
            "mode IN ('keycloak','native')", name='ck_auth_installation_mode'
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(16))
    bootstrap_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('auth_account.id')
    )
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthThrottle(Base):
    """Shared, fail-closed password attempt counters; keys contain no raw email/IP."""

    __tablename__ = 'auth_throttle'

    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=1)
