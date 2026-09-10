"""Explicit links between external identities and OpenHands users."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class ExternalIdentity(Base):
    __tablename__ = 'external_identities'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), index=True
    )
    connection: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str] = mapped_column(String(2048))
    subject: Mapped[str] = mapped_column(String(512))

    __table_args__ = (
        UniqueConstraint(
            'connection', 'issuer', 'subject', name='uq_external_identity'
        ),
    )
