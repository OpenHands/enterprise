"""Auth provider configuration model.

A row describes a single login/integration endpoint: an OAuth2/OIDC provider
(IDP or git provider), the built-in password login, or a SAML IDP. Unlike the
earlier ``oauth_providers`` table (one flat set of OAuth2-only columns, with
non-OAuth rows like the integrated/password IDP leaving most of them ``NULL``
and a ``provider_category`` sentinel string standing in for "this isn't
really OAuth2"), the protocol-specific configuration lives in ``details`` --
a ``storage.auth_provider_details.AuthProviderDetails`` discriminated union
-- so each row only ever carries the fields its protocol actually needs.

``kind`` mirrors ``type(details).__name__`` (``"OAuthProviderDetails"`` /
``"PasswordProviderDetails"`` / ``"SamlProviderDetails"``) as a plain,
unencrypted SQL column purely so rows can be filtered by protocol
(``WHERE kind = ...``) without decrypting ``details`` -- see ``_sync_kind``.
It is never set independently of ``details``.

``details`` is stored as a plain (unencrypted, human-readable) JSON column --
unlike ``EncryptedJSON``/``LLMProfiles``/``ProviderConnections``, the column
itself is *not* the encryption boundary. Only the handful of secret fields
inside ``details`` (e.g. ``OAuthProviderDetails.client_secret``,
``SamlProviderDetails.sp_private_key``) are encrypted, each on its own, via
the ``field_serializer``/``field_validator`` pair defined right on those
fields in ``storage.auth_provider_details`` -- see
``storage.encrypt_utils.create_json_with_encrypted_secrets_type_decorator``
for how binding/reading the column drives that. Reading the column back
validates the JSON into the correct ``AuthProviderDetails`` subclass, so
``provider.details`` is always a real typed object, never a bare ``dict``.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Identity, String, Text
from sqlalchemy.orm import Mapped, mapped_column, validates

from storage.auth_provider_details import AuthProviderDetails
from storage.base import Base
from storage.encrypt_utils import create_json_with_encrypted_secrets_type_decorator

# Default ``color`` for a row that hasn't been given a brand color yet --
# see migration 188. Duplicated as a literal there (not imported) so the
# migration's data shape stays fixed in time even if this default changes.
DEFAULT_AUTH_PROVIDER_COLOR = '#000000'


class AuthProvider(Base):
    """Per-provider login/integration configuration.

    Seeded by migration 186 (renamed+reshaped from the earlier
    ``oauth_providers`` table) from environment variables / existing rows.
    Runtime config changes go through ``AuthProviderStore``.
    """

    __tablename__ = 'auth_providers'

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    # Mirrors type(details).__name__ -- see _sync_kind below. Never set
    # directly; always derived from whatever is assigned to `details`.
    kind: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    # True for login IDPs, False for git providers.
    is_idp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Raw SVG markup for this provider's login button (see migration 188).
    # Client-supplied, not validated server-side -- same contract as
    # ``InstanceSettings.logo``. ``NULL`` until an admin sets one; the
    # frontend falls back to a generic icon.
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CSS color string for the login button. Every row needs *some* color to
    # render with, so this defaults to black rather than ``NULL``.
    color: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DEFAULT_AUTH_PROVIDER_COLOR
    )
    # Whether this row may be used to authenticate or link at all. A row is
    # created disabled (see migration 195) so an admin can fill in a
    # half-configured provider -- e.g. from a preset with placeholder URLs --
    # without it going live on the login screen. Disabling a row only stops
    # new flows: sessions and tokens already minted through it keep working,
    # so an admin cannot lock everyone out by flipping this flag.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Whether a brand-new user may be created the first time someone
    # authenticates through this row, or whether only an already-linked
    # account may sign in through it -- see
    # ``server.auth.provider_identity.resolve_or_create_user``.
    allow_registration: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Initial ``User.allow_match_by_email`` for users created by a login
    # through this row -- see
    # ``server.auth.provider_identity.resolve_or_create_user``.
    allow_match_by_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    details: Mapped[AuthProviderDetails] = mapped_column(
        create_json_with_encrypted_secrets_type_decorator(AuthProviderDetails),
        nullable=False,
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

    @validates('details')
    def _sync_kind(self, key: str, details: AuthProviderDetails) -> AuthProviderDetails:
        """Keep ``kind`` in lockstep with ``details`` -- the only source of truth.

        Fires on every assignment (construction *and* later mutation, e.g.
        the admin API's ``AuthProviderStore.update``), so ``kind`` can never
        drift from the type of the object actually stored in ``details``.
        """
        self.kind = type(details).__name__
        return details
