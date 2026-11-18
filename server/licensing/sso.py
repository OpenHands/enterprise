"""The license gate for single sign-on login providers.

A superadmin can add or turn on a SAML or OIDC login provider only when the
license includes SSO. SSO that is already on keeps working and can still be
edited, so nobody is locked out of sign-in.
"""

from __future__ import annotations

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.constants import KEYCLOAK_CLIENT_ID
from server.licensing.limits import LicenseLimitError
from server.licensing.service import get_license_service
from storage.auth_provider_details import (
    AuthProviderDetails,
    OAuthProviderDetails,
    SamlProviderDetails,
)


class SsoNotLicensedError(LicenseLimitError):
    def __init__(self) -> None:
        super().__init__("Your license doesn't include SSO.")


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


def is_sso_login(*, is_idp: bool, enabled: bool, details: AuthProviderDetails) -> bool:
    """Whether a provider row lets users sign in through single sign-on."""
    return is_idp and enabled and is_sso_provider(details)


def check_sso_licensed() -> None:
    """Raise ``SsoNotLicensedError`` when the license doesn't include SSO."""
    if not get_license_service().has_feature('sso'):
        raise SsoNotLicensedError()
