"""SCIM 2.0 users-only: credentials and directory records (migration 220).

A ``ScimCredential`` is a dedicated bearer token bound to exactly one
organization and one login provider. It is never an ``api_keys`` row, so it
cannot authenticate anything except the SCIM routes, and an API key cannot
authenticate those.

A ``ScimUser`` is one organization's directory record for a person. Its ``id``
is the SCIM resource id: minted here, never the internal ``user.id``, and
scoped to the organization, so the same person provisioned by two
organizations has two unrelated records.

``deactivated_at`` is the durable deactivation marker. It outlives the
membership row it removed, which is what lets the auth and re-grant guards
(``storage.scim_store.ScimStore.is_deactivated``) refuse to give the access
back until the directory reactivates the record. An inactive record without
it is one whose creation has not finished, and denies nothing.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base

# How the linked account came to be a member of the organization.
MEMBERSHIP_ORIGIN_SCIM = 'scim'
MEMBERSHIP_ORIGIN_PREEXISTING = 'preexisting'


class ScimCredential(Base):
    __tablename__ = 'scim_credential'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), nullable=False, index=True
    )
    # The corporate SSO provider whose email-matching policy governs linking.
    auth_provider_id: Mapped[int] = mapped_column(
        ForeignKey('auth_providers.id', ondelete='CASCADE'), nullable=False
    )
    # An operator's label, to tell credentials apart during a rotation.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # SHA-256 of the token. The token itself is shown once and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Written at most once a minute (``ScimStore.get_credential_by_token``).
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScimUser(Base):
    __tablename__ = 'scim_user'
    __table_args__ = (
        Index(
            'uq_scim_user_org_user_name',
            'org_id',
            text('lower(user_name)'),
            unique=True,
        ),
        Index(
            'uq_scim_user_org_external_id',
            'org_id',
            'external_id',
            unique=True,
            postgresql_where=text('external_id IS NOT NULL'),
        ),
        Index(
            'uq_scim_user_org_user_id',
            'org_id',
            'user_id',
            unique=True,
            postgresql_where=text('user_id IS NOT NULL'),
        ),
        Index('uq_scim_user_org_email', 'org_id', 'email', unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), nullable=False
    )
    # SET NULL, not CASCADE: deleting the account must not delete the
    # deactivation marker. The guards also match on ``email`` for that case.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('user.id', ondelete='SET NULL'), nullable=True
    )
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Lowercased, like every email ``UserStore`` matches on. Set at creation
    # and never changed: it is what a record is linked by, and what the
    # deactivation marker of an unlinked record matches on.
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    given_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    membership_origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
