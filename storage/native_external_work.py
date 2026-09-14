"""Durable native provisioning and cleanup, independent of deletable profiles."""

from datetime import datetime
from typing import TypedDict
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.encrypt_utils import EncryptedJSON
from storage.native_auth import now_utc


class NativeExternalPayload(TypedDict, total=False):
    member_key: str


class NativeExternalWork(Base):
    __tablename__ = 'native_external_work'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('auth_account.id'), index=True
    )
    org_id: Mapped[UUID | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    # Random keys are persisted encrypted before creating external resources.
    # Retrying a crashed request can retrieve the same key, never mint another.
    payload: Mapped[NativeExternalPayload] = mapped_column(EncryptedJSON, default=dict)
    claim_id: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
