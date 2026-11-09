"""Which login providers count as single sign-on for the license."""

from __future__ import annotations

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.constants import KEYCLOAK_CLIENT_ID
from storage.auth_provider_details import (
    AuthProviderDetails,
    OAuthProviderDetails,
    SamlProviderDetails,
)


def is_sso_provider(details: AuthProviderDetails) -> bool:
    """Whether a provider is a single sign-on identity provider.

    That is SAML, or a direct OIDC/OAuth identity provider (Okta, Google, ...).
    Keycloak's own rows and git providers don't count.
    """
    if isinstance(details, SamlProviderDetails):
        return True
    return (
        isinstance(details, OAuthProviderDetails)
        and details.provider_type == ProviderType.ENTERPRISE_SSO
        and details.legacy_keycloak_type is None
        and not (KEYCLOAK_CLIENT_ID and details.client_id == KEYCLOAK_CLIENT_ID)
    )
