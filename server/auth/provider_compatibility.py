"""Compatibility imports for the existing Keycloak broker."""

from server.auth.keycloak.providers import (
    resolve_broker_actor as resolve_broker_actor,
)
from server.auth.keycloak.providers import (
    resolve_broker_email as resolve_broker_email,
)
from server.auth.keycloak.providers import (
    unlink_broker_provider as unlink_broker_provider,
)
from server.auth.keycloak.providers import (
    user_from_broker_token as user_from_broker_token,
)
