from __future__ import annotations

from functools import lru_cache

from openhands_extensions import (
    IntegrationCatalogEntry,
    list_integration_catalog_models,
)

from .first_party_connectors import enterprise_first_party_connectors
from .integration_catalog import first_connection_defaults
from .models import ConnectionDefaults, ManagedConnector
from .repository import repository


def _is_dynamic_oauth(defaults: ConnectionDefaults) -> bool:
    """OAuth2 connector with no catalog authorizationUrl (server-managed OAuth)."""
    if defaults.authStrategy != "oauth2":
        return False
    oauth_config = defaults.oauthConfig
    if oauth_config and oauth_config.dynamicOAuth:
        return True
    return not oauth_config or not oauth_config.authorizationUrl


def _is_default_managed_connector(entry: IntegrationCatalogEntry) -> bool:
    # Tools come from MCP `tools/list` (live) or HTTP OpenAPI (regenerated on
    # save). An HTTP connector without an openApiUrl has no tool source, so we
    # skip it rather than surface a zero-tool default.
    defaults = first_connection_defaults(entry)
    if defaults.provider == "http" and defaults.apiBaseUrl:
        return bool(defaults.openApiUrl)
    if defaults.provider == "mcp" and defaults.serverUrl:
        return True
    return False


def _to_default_managed_connector(entry: IntegrationCatalogEntry) -> ManagedConnector:
    defaults = first_connection_defaults(entry)
    auth_modes = defaults.authModes
    if auth_modes and all(auth_modes):
        resolved_auth_modes = auth_modes
    else:
        resolved_auth_modes = [defaults.authStrategy]
    return ManagedConnector(
        slug=entry.id,
        name=entry.name,
        description=entry.description,
        logoUrl=entry.logoUrl,
        iconBg=entry.iconBg,
        iconColor=entry.iconColor,
        appUrl=entry.appUrl,
        docsUrl=entry.docsUrl,
        categories=entry.categories or [],
        authModes=resolved_auth_modes,
        authStrategy=defaults.authStrategy,
        provider=defaults.provider,
        credentialLabel=defaults.credentialLabel or f"{entry.name} credential",
        credentialPlaceholder=defaults.credentialPlaceholder
        or f"Paste your {entry.name} credential",
        credentialHelp=defaults.credentialHelp
        or f"Credential required by {entry.name}.",
        apiKeyHeaderName=(defaults.apiKeyHeaderName or "").strip() or None,
        apiBaseUrl=defaults.apiBaseUrl,
        openApiUrl=defaults.openApiUrl,
        serverUrl=defaults.serverUrl,
        oauthConfig=defaults.oauthConfig,
        connectionModel=defaults.connectionModel,
        # Default connectors ship with no tools; tools are discovered (MCP) or
        # generated from openApiUrl (HTTP) on connect/save/index.
        tools=[],
        # Static OAuth2 connectors need an admin to supply client credentials
        # before users can connect. Dynamic OAuth connectors (no catalog
        # authorizationUrl) delegate the entire OAuth dance to the MCP server
        # via protected-resource and authorization-server metadata — there is
        # nothing for an admin to pre-configure, so they are immediately ready.
        oauthConfigured=defaults.authStrategy != "oauth2"
        or _is_dynamic_oauth(defaults),
    )


@lru_cache(maxsize=1)
def default_managed_connectors() -> tuple[ManagedConnector, ...]:
    extension_defaults = tuple(
        _to_default_managed_connector(entry)
        for entry in list_integration_catalog_models()
        if _is_default_managed_connector(entry)
    )
    known = {connector.slug for connector in extension_defaults}
    first_party = tuple(
        connector
        for connector in enterprise_first_party_connectors()
        if connector.slug not in known
    )
    return extension_defaults + first_party


def default_managed_connector(slug: str) -> ManagedConnector | None:
    return next(
        (
            connector
            for connector in default_managed_connectors()
            if connector.slug == slug
        ),
        None,
    )


def get_managed_connector(slug: str) -> ManagedConnector | None:
    stored = repository.get_managed_connector(slug)
    catalog_default = default_managed_connector(slug)
    if stored and not stored.connectionModel and catalog_default:
        return stored.model_copy(
            update={"connectionModel": catalog_default.connectionModel}
        )
    return stored or catalog_default


def list_managed_connectors(include_defaults: bool = True) -> list[ManagedConnector]:
    connectors = repository.list_managed_connectors()
    catalog_defaults = {
        connector.slug: connector for connector in default_managed_connectors()
    }
    connectors = [
        connector.model_copy(
            update={"connectionModel": catalog_defaults[connector.slug].connectionModel}
        )
        if not connector.connectionModel and connector.slug in catalog_defaults
        else connector
        for connector in connectors
    ]
    # OAuth2 connectors may have a stale oauthConfigured flag (the DB
    # encrypted_oauth_client column can be NULL even when OAuth works —
    # e.g. credentials were stored then a subsequent save cleared them).
    # If any connection exists for an OAuth2 connector, the client was
    # necessarily configured, so infer oauthConfigured=True.
    if connectors:
        connected_slugs = {
            str(value)
            for conn in repository.list_connections()
            for value in (conn.get("integrationKey"), conn.get("provider"))
            if value
        }
        for connector in connectors:
            if (
                connector.authStrategy == "oauth2"
                and not connector.oauthConfigured
                and connector.slug in connected_slugs
            ):
                connector.oauthConfigured = True
    if include_defaults:
        registered = {connector.slug for connector in connectors}
        connectors = [
            *connectors,
            *[
                connector
                for connector in catalog_defaults.values()
                if connector.slug not in registered
            ],
        ]
    return sorted(connectors, key=lambda connector: connector.name.lower())
