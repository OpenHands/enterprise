"""Selected browser proof for existing Jira account-linking flows."""

from collections.abc import Mapping
from typing import Protocol

from fastapi import HTTPException, Request
from pydantic import JsonValue


class IntegrationStateStore(Protocol):
    def delete(self, name: str) -> object: ...


class IntegrationLinkPolicy(Protocol):
    def require_provider_connections(self) -> None: ...

    async def session_state(
        self, request: Request, account_id: str
    ) -> dict[str, str]: ...

    async def validate_callback(
        self,
        request: Request,
        state: Mapping[str, JsonValue],
        store: IntegrationStateStore,
        key: str,
    ) -> None: ...


class KeycloakIntegrationLinkPolicy:
    def require_provider_connections(self) -> None:
        return None

    async def session_state(self, request: Request, account_id: str) -> dict[str, str]:
        return {}

    async def validate_callback(
        self,
        request: Request,
        state: Mapping[str, JsonValue],
        store: IntegrationStateStore,
        key: str,
    ) -> None:
        # The existing Keycloak flow retains its OAuth state validation.
        return None


class OpenHandsIntegrationLinkPolicy:
    def require_provider_connections(self) -> None:
        raise HTTPException(
            409, 'Git provider connections are unavailable without Keycloak'
        )

    async def session_state(self, request: Request, account_id: str) -> dict[str, str]:
        from server.services.native_integration_auth import native_integration_session

        return {
            'native_session_id': await native_integration_session(request, account_id)
        }

    async def validate_callback(
        self,
        request: Request,
        state: Mapping[str, JsonValue],
        store: IntegrationStateStore,
        key: str,
    ) -> None:
        from server.services.native_integration_auth import (
            verify_native_integration_session,
        )

        await verify_native_integration_session(request, state)
        store.delete(key)
