"""Single-use account enrollment and organization membership invitations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.native_auth import now_utc


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
