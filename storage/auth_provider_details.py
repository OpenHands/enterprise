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
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pydantic import (
    Field,
    SecretStr,
    SerializationInfo,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from openhands.app_server.constants import validate_secret_name
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.env_var_validation import validate_env_var_name
from openhands.sdk.utils.models import DiscriminatedUnionMixin
from storage.encrypt_utils import decrypt_value, encrypt_value

# RSA key size for auto-generated SAML SP signing keys -- 2048 bits is the
# minimum most IdPs accept and matches what Keycloak/Okta/Auth0 generate by
# default for their own SP integrations.
SP_KEY_SIZE_BITS = 2048

# Validity window for the auto-generated, self-signed SP certificate.
#
# SAML does not validate this cert against a CA chain -- the IdP admin
# imports the exact bytes we give them and trusts that directly (the same
# model Okta/Auth0/Keycloak use for their own SP certs), so there is no
# browser-style "renew before it expires or the padlock breaks" pressure.
# What *is* expensive is rotation: every IdP an admin has connected this
# provider to needs its trust store updated by hand when the cert changes.
# A long validity window (10 years) means rotation is a deliberate admin
# action (not yet built -- would need to replace both fields together, see
# ``server.routes.oauth_providers_admin._ensure_sp_keypair``), never a
# surprise expiry.
SP_CERT_VALID_DAYS = 3650
# Backdated slightly so a freshly generated cert is never rejected as
# "not yet valid" by an IdP whose clock is a few seconds ahead of ours.
SP_CERT_VALID_FROM_SKEW = timedelta(minutes=5)
# Purely a label on the self-signed cert -- SAML has no hostname/identity
# validation against it (unlike a TLS cert), so this is cosmetic, not
# security relevant. Not derived from ``sp_entity_id`` since the cert is
# generated at row-creation time, before an admin may have set one.
SP_CERT_COMMON_NAME = 'openhands-saml-sp'


def generate_sp_keypair() -> tuple[str, str]:
    """Generate a new RSA key pair and self-signed X.509 cert for a SAML SP.

    Returns ``(private_key_pem, certificate_pem)``. Called by
    ``server.routes.oauth_providers_admin`` the first time a row becomes (or
    is created as) ``SamlProviderDetails`` with no key yet -- admins never
    type an SP private key themselves, see ``SamlProviderDetails.sp_x509_cert``.

    A bare public key is not sufficient here: the SP metadata document's
    ``<ds:X509Certificate>`` element, and most IdPs' "upload your SP cert"
    forms, want an actual X.509 certificate (subject/issuer/serial/validity
    wrapped around the key), not a ``SubjectPublicKeyInfo`` blob. The cert is
    self-signed -- see ``SP_CERT_VALID_DAYS`` for why that's the norm here,
    not a shortcut.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=SP_KEY_SIZE_BITS)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, SP_CERT_COMMON_NAME)]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - SP_CERT_VALID_FROM_SKEW)
        .not_valid_after(now + timedelta(days=SP_CERT_VALID_DAYS))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return private_pem, cert_pem


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


GIT_PROVIDER_TYPES: frozenset[ProviderType] = frozenset(
    {
        ProviderType.GITHUB,
        ProviderType.GITLAB,
        ProviderType.BITBUCKET,
        ProviderType.BITBUCKET_DATA_CENTER,
        ProviderType.FORGEJO,
        ProviderType.AZURE_DEVOPS,
    }
)


class OAuthProviderDetails(AuthProviderDetails):
    """OAuth2/OIDC configuration. A row is a git provider when its
    ``provider_type`` is in ``GIT_PROVIDER_TYPES``, whether or not it is also a
    login IDP (``AuthProvider.is_idp``). Brand
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
    # Set on the per-brand login rows migration 189 seeds
    # (``title='Log in with GitHub'`` etc.; ``provider_type`` itself is
    # ``enterprise_sso``): their ``token_url``/credentials are Keycloak's own
    # and Keycloak routes to that brand's federated identity via
    # ``kc_idp_hint``, but its OIDC response only carries a Keycloak token.
    # The value is the brand whose real token must additionally be fetched
    # from Keycloak's identity-brokering endpoint
    # (``TokenManager.get_idp_tokens_from_keycloak``) and stored under the
    # git-provider row of that ``provider_type``.
    legacy_keycloak_type: ProviderType | None = None
    # Name of the conversation secret that exposes a linked user's access
    # token for this provider to the sandbox (with OAuth v2 login enabled).
    # ``None`` keeps the token out of the sandbox. Several rows may share a
    # name; a user linked to more than one gets a single secret, see
    # ``SaasUserAuth.get_named_oauth_secrets``.
    named_secret: str | None = None

    @field_validator('legacy_keycloak_type')
    @classmethod
    def _legacy_keycloak_type_is_git_provider(
        cls, value: ProviderType | None
    ) -> ProviderType | None:
        if value == ProviderType.ENTERPRISE_SSO:
            raise ValueError('legacy_keycloak_type must be a git provider')
        return value

    @field_validator('named_secret', mode='before')
    @classmethod
    def _validate_named_secret(cls, value: Any) -> str | None:
        if value is None or value == '':
            return None
        validate_env_var_name(value, 'named_secret')
        validate_secret_name(value)
        return value

    @model_validator(mode='after')
    def _no_named_secret_for_keycloak_tokens(self) -> OAuthProviderDetails:
        # The token stored under such a row is Keycloak's own session token.
        if self.named_secret and self.legacy_keycloak_type:
            raise ValueError(
                'named_secret cannot be set on a row with legacy_keycloak_type'
            )
        return self

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

    ``sp_private_key``/``sp_x509_cert`` are never admin-supplied --
    ``generate_sp_keypair`` auto-generates them as a pair (see
    ``server.routes.oauth_providers_admin._ensure_sp_keypair``) the first
    time a row becomes SAML with no key yet, so they can't be entered
    wrong/mismatched and never need rotating by hand. ``sp_x509_cert`` is
    public by construction (it is served back from the unauthenticated SP
    metadata endpoint), so unlike ``sp_private_key`` it is stored and
    transmitted in the clear -- it is also what an admin copies into an IdP
    that wants the SP cert configured out of band (most read it from our
    metadata URL instead).
    """

    metadata_url: str | None = None
    idp_entity_id: str | None = None
    sso_url: str | None = None
    sso_binding: Literal['redirect', 'post'] = 'redirect'
    slo_url: str | None = None
    idp_x509_cert: str | None = None
    sp_private_key: SecretStr | None = None
    sp_x509_cert: str | None = None
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
