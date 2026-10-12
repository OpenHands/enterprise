"""Polymorphic, protocol-specific configuration for an ``auth_providers`` row.

``AuthProvider.details`` (see ``storage.auth_provider``) holds one of these
concrete subclasses, discriminated by ``kind`` -- a computed field that
``DiscriminatedUnionMixin`` always derives from the class name
(``"OAuthProviderDetails"``, ``"PasswordProviderDetails"``,
``"SamlProviderDetails"``). ``AuthProvider.kind`` mirrors this value as a
plain SQL column (see ``AuthProvider._sync_kind``) purely so it can be
filtered/indexed without decrypting every row's ``details`` blob -- it is
never set independently.

Secret fields (``client_secret``, ``sp_private_key``) are plain pydantic
``SecretStr`` with a ``field_serializer`` that checks
``info.context.get('expose_secrets')``, exactly mirroring
``Secrets.provider_tokens_serializer`` / ``custom_secrets_serializer``
(``openhands/app_server/secrets/secrets_models.py``) and ``MCPServer``'s own
secret fields: dumping with ``context={'expose_secrets': True}`` returns the
real value (used when persisting -- see
``storage.encrypt_utils.create_encrypted_json_type_decorator``), dumping
without it returns pydantic's masked ``SecretStr`` representation (used when
serving the admin API). One model, two dump calls -- no separate
``has_client_secret`` bookkeeping required.
"""

from __future__ import annotations

from abc import ABC
from typing import Literal

from pydantic import (
    Field,
    SecretStr,
    SerializationInfo,
    field_serializer,
    field_validator,
)
from pydantic.json import pydantic_encoder

from openhands.app_server.integrations.service_types import ProviderType
from openhands.sdk.utils.models import DiscriminatedUnionMixin

# Default clock-drift margin (seconds). Expired access tokens are still
# refreshable via the refresh token; this margin only controls how early we
# treat a still-valid access token as expired so a refresh kicks in before
# the IDP actually rejects it.
DEFAULT_PERMITTED_DRIFT_SECONDS = 60


def _serialize_secret(value: SecretStr | None, info: SerializationInfo) -> str | None:
    if value is None:
        return None
    expose_secrets = bool(info.context and info.context.get('expose_secrets'))
    return value.get_secret_value() if expose_secrets else pydantic_encoder(value)


def _empty_string_clears(value: SecretStr | str | None) -> SecretStr | str | None:
    """Treat an explicit ``""`` the same as "no secret configured".

    Backs the admin API's "send ``\"\"`` to clear a configured secret"
    contract (see ``server.routes.oauth_providers_admin``): without this, an
    empty string would round-trip as ``SecretStr('')`` -- a
    configured-but-blank secret -- rather than ``None``.
    """
    if isinstance(value, SecretStr):
        return None if value.get_secret_value() == '' else value
    return None if value == '' else value


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
    def _clear_empty_client_secret(cls, value):
        return _empty_string_clears(value)

    @field_serializer('client_secret')
    def _dump_client_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_secret(value, info)


class PasswordProviderDetails(AuthProviderDetails):
    """The integrated/password IDP (``server.routes.idp``). No external
    endpoint to configure -- this row exists purely so the login path has an
    ``auth_providers`` id to carry in the ``openhands_auth`` cookie and to
    participate in ``is_idp`` listings like any other provider.
    """


class SamlProviderDetails(AuthProviderDetails):
    """SAML 2.0 IDP configuration."""

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
    def _clear_empty_sp_private_key(cls, value):
        return _empty_string_clears(value)

    @field_serializer('sp_private_key')
    def _dump_sp_private_key(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_secret(value, info)
