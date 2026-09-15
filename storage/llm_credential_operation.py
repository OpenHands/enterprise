"""Recoverable credential issuance, including before account creation commits."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.encrypt_utils import EncryptedJSON


class LlmCredentialOperation(Base):
    __tablename__ = 'llm_credential_operation'
    __table_args__ = (
        UniqueConstraint('org_id', 'request_hash', name='uq_llm_credential_request'),
        UniqueConstraint('key_hash', name='uq_llm_credential_key'),
        CheckConstraint(
            "status IN ('pending', 'issued', 'revoked')",
            name='ck_llm_credential_status',
        ),
        Index(
            'ix_llm_credential_retirement',
            'org_id',
            postgresql_where=text(
                'replaces_key_hash IS NOT NULL AND activated_at IS NOT NULL AND retired_at IS NULL'
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Bootstrap intent must survive an uncommitted or rolled-back account transaction.
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON, nullable=False)
    replaces_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending')
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
