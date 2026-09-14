"""Selected availability of provider account connections."""

from typing import Protocol

from fastapi import HTTPException


class IntegrationLinkPolicy(Protocol):
    def require_provider_connections(self) -> None: ...


class KeycloakIntegrationLinkPolicy:
    def require_provider_connections(self) -> None:
        return None


class OpenHandsIntegrationLinkPolicy:
    def require_provider_connections(self) -> None:
        raise HTTPException(
            409, 'Git provider connections are unavailable without Keycloak'
        )
