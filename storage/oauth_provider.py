"""OAuth provider configuration model.

A row describes a single OAuth/OIDC endpoint: either an identity provider
(``is_idp=True``, used for login) or a git provider (``is_idp=False``, whose
tokens are consumed by integrations). A provider is never both — SaaS "GitHub
as both" is two rows (one IDP row, one git-provider row).

``provider_category`` reuses the existing ``ProviderType`` enum. The
``ENTERPRISE_SSO`` value covers any OIDC IDP that is not also a git provider
(e.g. Keycloak itself).

Secret columns (``client_secret``) use the existing ``EncryptedJSON``
TypeDecorator so the credential is encrypted at rest by the same JWE service
used elsewhere in the codebase.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Identity,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.encrypt_utils import EncryptedJSON

# Default clock-drift margin (seconds). Expired access tokens are still
# refreshable via the refresh token; this margin only controls how early we
# treat a still-valid access token as expired so a refresh kicks in before the
# IDP actually rejects it.
DEFAULT_PERMITTED_DRIFT_SECONDS = 60


class OAuthProvider(Base):
    """Per-provider OAuth/OIDC configuration.

    Seeded by migration 168 from environment variables (one row for the IDP —
    Keycloak — and one row per configured git provider). Runtime config changes
    go through ``OAuthProviderStore``.
    """

    __tablename__ = 'oauth_providers'

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    # Reuses the existing ProviderType enum values ('github', 'gitlab', ...,
    # 'enterprise_sso' for non-git OIDC IDPs).
    provider_category: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # True for login IDPs, False for git providers.
    is_idp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted at rest via EncryptedJSON (stored as String).
    client_secret: Mapped[dict[str, str] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    authorization_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    token_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    userinfo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Space- or list-separated OAuth scopes for this provider.
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    permitted_drift_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_PERMITTED_DRIFT_SECONDS
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
