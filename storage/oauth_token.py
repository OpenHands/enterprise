"""OAuth token model — per-user credential pair for one provider.

Denormalized (``user_id`` + ``oauth_provider_id`` directly) so token reads need
no join. Expiry uses ``DateTime(timezone=True)`` with ``NULL`` meaning
"never expires" (no ``0`` sentinel).

Secret columns (``access_token``, ``refresh_token``) use the existing
``EncryptedJSON`` TypeDecorator. A token string is wrapped in a single-key dict
``{"v": "<token>"}`` so it round-trips through ``EncryptedJSON`` (which expects
a JSON-serializable value) while the store layer exposes plain strings to
callers.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Identity, Index
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.encrypt_utils import EncryptedJSON


class OAuthToken(Base):
    """Stored access/refresh token pair for a user at one OAuth provider."""

    __tablename__ = 'oauth_tokens'

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    oauth_provider_id: Mapped[int] = mapped_column(
        ForeignKey('oauth_providers.id', ondelete='CASCADE'), nullable=False
    )
    access_token: Mapped[dict[str, str] | None] = mapped_column(
        EncryptedJSON, nullable=False
    )
    refresh_token: Mapped[dict[str, str] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    # NULL == never expires.
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
            'uq_oauth_tokens_user_provider',
            'user_id',
            'oauth_provider_id',
            unique=True,
        ),
    )
