"""OAuth provider-user link model.

Maps a provider's external user identity (``external_subject_id``, the OIDC
``sub``) to our internal ``User``. This is the identity-resolution table used
on login lookup — separate from credentials (``oauth_tokens``) so it survives
token rotation.

Unique on ``(oauth_provider_id, external_subject_id)``: one external identity
per provider maps to exactly one internal user.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class OAuthProviderUser(Base):
    """External-identity → internal-user mapping for one OAuth provider."""

    __tablename__ = 'oauth_provider_users'

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    oauth_provider_id: Mapped[int] = mapped_column(
        ForeignKey('oauth_providers.id', ondelete='CASCADE'), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    # OIDC ``sub`` / git provider user id.
    external_subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index(
            'uq_oauth_provider_users_provider_subject',
            'oauth_provider_id',
            'external_subject_id',
            unique=True,
        ),
        Index(
            'ix_oauth_provider_users_user_provider',
            'user_id',
            'oauth_provider_id',
        ),
    )
