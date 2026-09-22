from __future__ import annotations

from typing import cast

from openhands_extensions import (
    HttpConnectionOption,
    IntegrationCatalogEntry,
    IntegrationConnectionOption,
    McpConnectionOption,
)

from .models import AuthStrategy, ConnectionDefaults, OAuthConfig


def connection_defaults_from_option(
    option: IntegrationConnectionOption,
) -> ConnectionDefaults:
    oauth_config = (
        OAuthConfig.model_validate(option.auth.oauth.model_dump(exclude_none=True))
        if option.auth.oauth
        else None
    )
    http = option.http if isinstance(option, HttpConnectionOption) else None
    transport = option.transport if isinstance(option, McpConnectionOption) else None
    server_url = getattr(transport, "url", None) if transport else None
    return ConnectionDefaults(
        provider=option.provider,
        authModes=cast(list[AuthStrategy] | None, option.auth.authModes),
        authStrategy=option.auth.strategy,
        credentialLabel=option.auth.credentialLabel,
        credentialPlaceholder=option.auth.credentialPlaceholder,
        credentialHelp=option.auth.credentialHelp,
        apiKeyHeaderName=option.auth.apiKeyHeaderName,
        apiKeyOptional=option.auth.apiKeyOptional,
        apiBaseUrl=http.apiBaseUrl if http else None,
        serverUrl=server_url,
        openApiUrl=http.openApiUrl if http else None,
        oauthConfig=oauth_config,
        connectionModel=option.connectionModel,
    )


def first_connection_defaults(entry: IntegrationCatalogEntry) -> ConnectionDefaults:
    return connection_defaults_from_option(entry.connectionOptions[0])
