"""Native credentials outlive the disposable application User profile."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

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
    credential_version: Mapped[int] = mapped_column(default=1)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )


class BrowserSession(Base):
    __tablename__ = 'browser_session'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey('auth_account.id'), index=True)
    session_version: Mapped[int] = mapped_column()
    credential_version: Mapped[int] = mapped_column()
    auth_method: Mapped[str] = mapped_column(String(32), default='password')
    auth_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountInvitation(Base):
    __tablename__ = 'account_invitation'
    __table_args__ = (
        CheckConstraint(
            '(org_id IS NULL) = (org_role_id IS NULL)',
            name='ck_account_invitation_scope',
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    reserved_account_id: Mapped[UUID] = mapped_column(default=uuid4)
    normalized_email: Mapped[str] = mapped_column(String(320), index=True)
    display_email: Mapped[str] = mapped_column(String(320))
    creator_account_id: Mapped[UUID] = mapped_column(ForeignKey('auth_account.id'))
    accepted_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('auth_account.id')
    )
    # Preserve invalidated scope when an organization is deleted; redemption checks it.
    org_id: Mapped[UUID | None] = mapped_column()
    org_role_id: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthChallenge(Base):
    __tablename__ = 'auth_challenge'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('auth_account.id'), index=True
    )
    credential_version: Mapped[int | None] = mapped_column()
    creator_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('auth_account.id')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
