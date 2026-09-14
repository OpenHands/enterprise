"""Short-lived, encrypted SAML transactions and cross-worker replay claims."""

from datetime import datetime
from typing import TypedDict
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.encrypt_utils import EncryptedJSON


class SamlTransactionContext(TypedDict, total=False):
    invitation_token: str | None
    reauthenticate: bool
    started_at: str


class SamlVerifiedClaims(TypedDict):
    subject: str
    email: str
    auth_time: str
    session_expiry_bound: str | None


class SamlTransaction(Base):
    __tablename__ = 'saml_transaction'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    relay_digest: Mapped[str] = mapped_column(String(64), unique=True)
    browser_digest: Mapped[str] = mapped_column(String(64), unique=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    connection_id: Mapped[str] = mapped_column(String(64))
    configuration_digest: Mapped[str] = mapped_column(String(64))
    return_path: Mapped[str] = mapped_column(String(2048))
    context: Mapped[SamlTransactionContext] = mapped_column(EncryptedJSON)
    verified_claims: Mapped[SamlVerifiedClaims | None] = mapped_column(EncryptedJSON)
    link_account_id: Mapped[UUID | None] = mapped_column(ForeignKey('auth_account.id'))
    link_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('browser_session.id')
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SamlReplay(Base):
    __tablename__ = 'saml_replay'

    id_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
