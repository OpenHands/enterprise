"""Opt-in customer gateway: org config and per-member encrypted credential (migration 219)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class CustomerGatewayOrgConfig(Base):
    __tablename__ = 'customer_gateway_org_config'

    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), primary_key=True
    )
    gateway_id: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set only on an OFF->ON transition: sandboxes born before it are refused. Catalog
    # edits (updated_at) do not invalidate existing conversations.
    enabled_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomerGatewayMemberCredential(Base):
    """Customer key is ciphertext only; no code path returns it outside the relay."""

    __tablename__ = 'customer_gateway_member_credential'
    __table_args__ = (
        ForeignKeyConstraint(
            ['org_id', 'user_id'],
            ['org_member.org_id', 'org_member.user_id'],
            ondelete='CASCADE',
        ),
    )

    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    ciphertext: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    credential_version: Mapped[int] = mapped_column(nullable=False, default=1)
    policy_version: Mapped[int] = mapped_column(nullable=False, default=1)
    allowed_models: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
