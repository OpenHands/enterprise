"""Polymorphic, protocol-specific configuration for an ``auth_providers`` row.

``AuthProvider.details`` (see ``storage.auth_provider``) holds one of these
concrete subclasses, discriminated by ``kind`` -- a computed field that
``DiscriminatedUnionMixin`` always derives from the class name
(``"OAuthProviderDetails"``, ``"PasswordProviderDetails"``,
``"SamlProviderDetails"``). ``AuthProvider.kind`` mirrors this value as a
plain SQL column (see ``AuthProvider._sync_kind``) purely so it can be
filtered/indexed without parsing every row's ``details`` blob -- it is never
set independently.

``AuthProvider.details`` itself is a *plain* (unencrypted) JSON column --
unlike ``LLMProfiles``/``ProviderConnections``, whose whole column is the
encryption boundary -- so each secret field here (``client_secret``,
``sp_private_key``) is responsible for encrypting *itself*:

* ``storage.encrypt_utils.create_json_with_encrypted_secrets_type_decorator``
  dumps with ``context={'for_storage': True}`` when binding the column for a
  write. ``_serialize_secret_field`` responds to that by swapping the real
  value for its own JWE ciphertext (``storage.encrypt_utils.encrypt_value``)
  -- every other field in ``details`` is left as plain JSON, readable
  straight out of the database.
* That same decorator's ``process_result_value`` validates the row back with
  ``context={'from_storage': True}``. ``_decrypt_secret_field`` responds by
  decrypting that ciphertext back into the real value.
* Any other construction (a brand-new object, or the admin API's create/
  update request bodies) carries no such context, so the field is treated as
  an already-decrypted value -- exactly what an admin typing a new secret
  into a form sends. An explicit empty string (``""``) is normalized to
  ``None`` here, backing the admin API's "send `""` to clear a configured
  secret" contract.
* Any other *serialization* (notably the admin API's response model) returns
  pydantic's masked ``SecretStr`` placeholder when a secret is configured,
  ``None`` when it is not -- the two are distinguishable, but the real
  value, encrypted or not, is never echoed back either way.
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import (
    Field,
    SecretStr,
    SerializationInfo,
    ValidationInfo,
    computed_field,
    field_serializer,
    field_validator,
)

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.utils.models import DiscriminatedUnionMixin
from storage.encrypt_utils import decrypt_value, encrypt_value

# RSA key size for auto-generated SAML SP signing keys -- 2048 bits is the
# minimum most IdPs accept and matches what Keycloak/Okta/Auth0 generate by
# default for their own SP integrations.
SP_KEY_SIZE_BITS = 2048


def generate_sp_keypair() -> tuple[str, str]:
    """Generate a new RSA key pair for a SAML SP, PEM-encoded.

    Returns ``(private_key_pem, public_key_pem)``. Called by
    ``server.routes.oauth_providers_admin`` the first time a row becomes (or
    is created as) ``SamlProviderDetails`` with no key yet -- admins never
    type an SP private key themselves, see ``SamlProviderDetails.sp_public_key``.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=SP_KEY_SIZE_BITS)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


# Default clock-drift margin (seconds). Expired access tokens are still
# refreshable via the refresh token; this margin only controls how early we
# treat a still-valid access token as expired so a refresh kicks in before
# the IDP actually rejects it.
DEFAULT_PERMITTED_DRIFT_SECONDS = 60


def _decrypt_secret_field(value: Any, info: ValidationInfo) -> str | None:
    """Normalize a secret field on the way in -- see the module docstring."""
    if value is None or value == '':
        return None
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if info.context and info.context.get('from_storage'):
        return decrypt_value(value)
    return value


def _serialize_secret_field(
    value: SecretStr | None, info: SerializationInfo
) -> str | None:
    """Serialize a secret field for storage, or mask it everywhere else.

    Dumping without ``context={'for_storage': True}`` (e.g. the admin API's
    response model) returns pydantic's masked ``SecretStr`` placeholder
    (``"**********"``) when a secret is configured, ``None`` when it is not
    -- the two are distinguishable, so there is no separate
    ``has_client_secret``/``has_sp_private_key`` flag to maintain, and the
    real value is never echoed back either way.
    """
    if value is None:
        return None
    if info.context and info.context.get('for_storage'):
        return encrypt_value(value.get_secret_value())
    return str(value)


class AuthProviderDetails(DiscriminatedUnionMixin, ABC):
    """Base class for protocol-specific ``auth_providers.details`` payloads."""


class OAuthProviderDetails(AuthProviderDetails):
    """OAuth2/OIDC configuration -- covers both real login IDPs
    (``provider_type=ProviderType.ENTERPRISE_SSO``) and git providers
    (``provider_type`` one of the other ``ProviderType`` values). Brand
    (github/gitlab/.../enterprise_sso) used to be the SQL ``provider_category``
    column; it now lives here since it is OAuth-specific, not a property of
    every ``auth_providers`` row.
    """

    provider_type: ProviderType
    client_id: str
    client_secret: SecretStr | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: list[str] | None = None
    permitted_drift_seconds: int = DEFAULT_PERMITTED_DRIFT_SECONDS

    @field_validator('client_secret', mode='before')
    @classmethod
    def _normalize_client_secret(cls, value: Any, info: ValidationInfo):
        return _decrypt_secret_field(value, info)

    @field_serializer('client_secret')
    def _dump_client_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_secret_field(value, info)


class PasswordProviderDetails(AuthProviderDetails):
    """The integrated/password IDP (``server.routes.idp``). No external
    endpoint to configure -- this row exists purely so the login path has an
    ``auth_providers`` id to carry in the ``openhands_auth`` cookie and to
    participate in ``is_idp`` listings like any other provider.
    """


class SamlProviderDetails(AuthProviderDetails):
    """SAML 2.0 IDP configuration.

    ``sp_private_key`` is never admin-supplied -- ``generate_sp_keypair`` auto-
    generates it (see ``server.routes.oauth_providers_admin._ensure_sp_keypair``)
    the first time a row becomes SAML with no key yet, so it can't be entered
    wrong/mismatched and never needs rotating by hand. ``sp_public_key`` is
    derived from it on the fly rather than stored separately, so the two can
    never drift out of sync; it is what an admin copies into the IdP's SP
    metadata/ACS signing-certificate configuration.
    """

    metadata_url: str | None = None
    idp_entity_id: str | None = None
    sso_url: str | None = None
    sso_binding: Literal['redirect', 'post'] = 'redirect'
    slo_url: str | None = None
    idp_x509_cert: str | None = None
    sp_private_key: SecretStr | None = None
    name_id_format: str | None = None
    attribute_mapping: dict[str, str] = Field(default_factory=dict)

    @field_validator('sp_private_key', mode='before')
    @classmethod
    def _normalize_sp_private_key(cls, value: Any, info: ValidationInfo):
        return _decrypt_secret_field(value, info)

    @field_serializer('sp_private_key')
    def _dump_sp_private_key(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_secret_field(value, info)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sp_public_key(self) -> str | None:
        """The public half of ``sp_private_key``, PEM-encoded -- ``None``
        until a key has been generated. Read-only: setting it directly has
        no effect since it is never stored, only derived at read time.
        """
        if self.sp_private_key is None:
            return None
        try:
            private_key = serialization.load_pem_private_key(
                self.sp_private_key.get_secret_value().encode(), password=None
            )
        except ValueError:
            logger.warning('auth_provider_details:unparseable_sp_private_key')
            return None
        return (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
