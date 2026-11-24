"""Rows outlive both the signed bearer and RelayState acceptance windows."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class SamlConsumedMessage(Base):
    __tablename__ = 'saml_consumed_message'
    __table_args__ = (
        UniqueConstraint(
            'provider_id', 'message_kind', 'message_id', name='uq_saml_consumed_message'
        ),
        Index('ix_saml_consumed_message_expires_at', 'expires_at'),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey('auth_providers.id', ondelete='CASCADE'), nullable=False
    )
    message_kind: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
