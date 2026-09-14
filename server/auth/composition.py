"""Select a fixed authentication service bundle for this installation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from server.auth import auth_config

if TYPE_CHECKING:
    from server.auth.account_lookup import AccountLookup
    from server.auth.browser_policy import BrowserPolicy
    from server.auth.integration_link_policy import IntegrationLinkPolicy
    from server.auth.provider_actor_lookup import ProviderActorLookup
    from server.auth.request_auth import RequestAuth
    from server.services.account_profile_provisioning import AccountProfileProvisioning
    from server.services.admin_user_lifecycle_service import LifecycleOperations
    from server.services.managed_llm_provisioning import ManagedLlmProvisioning
    from server.services.org_invitation_service import OrgInvitationService


@dataclass(frozen=True)
class AuthServices:
    requests: RequestAuth
    browser: BrowserPolicy
    integrations: IntegrationLinkPolicy
    actors: ProviderActorLookup
    accounts: AccountLookup
    lifecycle: LifecycleOperations
    profiles: AccountProfileProvisioning
    provisioning: ManagedLlmProvisioning
    invitations: OrgInvitationService


def build_auth_services() -> AuthServices:
    """Construct stateless services without initializing an inactive provider."""
    from server.auth.account_lookup import (
        KeycloakAccountLookup,
        OpenHandsAccountLookup,
    )
    from server.auth.browser_policy import KeycloakBrowserPolicy, OpenHandsBrowserPolicy
    from server.auth.integration_link_policy import (
        KeycloakIntegrationLinkPolicy,
        OpenHandsIntegrationLinkPolicy,
    )
    from server.auth.keycloak_request_auth import KeycloakRequestAuth
    from server.auth.openhands_request_auth import OpenHandsRequestAuth
    from server.auth.provider_actor_lookup import (
        KeycloakProviderActorLookup,
        OpenHandsProviderActorLookup,
    )
    from server.services.account_profile_provisioning import (
        KeycloakAccountProfileProvisioning,
        OpenHandsAccountProfileProvisioning,
    )
    from server.services.admin_user_lifecycle_service import (
        KeycloakUserLifecycleService,
        OpenHandsUserLifecycleService,
    )
    from server.services.managed_llm_provisioning import (
        KeycloakManagedLlmProvisioning,
        OpenHandsManagedLlmProvisioning,
    )
    from server.services.org_invitation_service import (
        KeycloakOrgInvitationService,
        OpenHandsOrgInvitationService,
    )

    if auth_config.ENABLE_KEYCLOAK:
        return AuthServices(
            requests=KeycloakRequestAuth(),
            browser=KeycloakBrowserPolicy(),
            integrations=KeycloakIntegrationLinkPolicy(),
            actors=KeycloakProviderActorLookup(),
            accounts=KeycloakAccountLookup(),
            lifecycle=KeycloakUserLifecycleService(),
            profiles=KeycloakAccountProfileProvisioning(),
            provisioning=KeycloakManagedLlmProvisioning(),
            invitations=KeycloakOrgInvitationService(),
        )
    return AuthServices(
        requests=OpenHandsRequestAuth(),
        browser=OpenHandsBrowserPolicy(),
        integrations=OpenHandsIntegrationLinkPolicy(),
        actors=OpenHandsProviderActorLookup(),
        accounts=OpenHandsAccountLookup(),
        lifecycle=OpenHandsUserLifecycleService(),
        profiles=OpenHandsAccountProfileProvisioning(),
        provisioning=OpenHandsManagedLlmProvisioning(),
        invitations=OpenHandsOrgInvitationService(),
    )


@cache
def get_auth_services() -> AuthServices:
    """Reuse the installation's fixed bundle, never request identities or tokens."""
    return build_auth_services()
