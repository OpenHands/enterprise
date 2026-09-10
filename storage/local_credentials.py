"""Local credentials for the existing OpenHands account directory."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


def normalize_login_email(email: str) -> str:
    """Use case-insensitive login matching without removing dots or plus tags."""
    return email.strip().casefold()


class LocalCredentials(Base):
    __tablename__ = 'local_credentials'

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), primary_key=True
    )
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    must_change_password: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (
        CheckConstraint(
            'normalized_email = lower(trim(normalized_email)) '
            'AND length(normalized_email) > 0',
            name='ck_local_credentials_normalized_email',
        ),
    )
