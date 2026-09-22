from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from .config import get_default_config, normalize_public_base_url
from .deprecations import warn_deprecated
from .managed_connectors import get_managed_connector
from .models import (
    ConnectionResourceInput,
    IntegrationSpec,
    ManagedConnector,
    OAuthAuthorizationServerMetadata,
    OAuthConfig,
    OAuthConnection,
    OAuthConnectionMetadata,
    OAuthProtectedResourceMetadata,
    OAuthTokenResponse,
)
from .public_paths import public_api_path, public_ui_path
from .repository import repository
from .url_security import urlopen_no_redirect, validate_external_url


def is_dynamic_oauth(connector: ManagedConnector) -> bool:
    """An OAuth2 connector whose OAuth endpoints are discovered at runtime.

    These connectors (e.g. Superhuman Mail) advertise RFC 9728 protected-resource
    metadata from their MCP URL. That metadata identifies the authorization server,
    whose RFC 8414 metadata supplies the authorization/token endpoints. The hub
    discovers those endpoints at runtime instead of reading them from the catalog,
    and no admin pre-configuration (client credentials or registration URL) is
    needed.

    A connector is dynamic if either (a) its catalog entry has no
    ``authorizationUrl`` (pre-discovery), or (b) it was previously enriched
    with discovered endpoints and flagged with ``dynamicOAuth: true`` in its
    ``oauthConfig`` (post-discovery, e.g. loaded from the DB).
    """
    if connector.authStrategy != "oauth2":
        return False
    oauth_config = connector.oauthConfig
    if oauth_config and oauth_config.dynamicOAuth:
        return True
    return not oauth_config or not oauth_config.authorizationUrl


def well_known_metadata_url(identifier: str, document: str) -> str:
    """Insert a well-known metadata path before an identifier's path.

    RFC 9728 and RFC 8414 both derive metadata URLs by inserting the
    ``/.well-known/<document>`` segment between the origin and the identifier's
    path. Appending the segment to an MCP endpoint path produces a different,
    invalid URL (for example, ``/mcp/.well-known/...``).
    """
    validated = validate_external_url(identifier, purpose=f"{document} identifier")
    parsed = urllib.parse.urlsplit(validated)
    path = parsed.path or ""
    return urllib.parse.urlunsplit(
        parsed._replace(path=f"/.well-known/{document}{path}", fragment="")
    )


def fetch_oauth_metadata_document(url: str, purpose: str) -> dict[str, Any]:
    validated = validate_external_url(url, purpose=purpose)
    request = urllib.request.Request(validated, headers={"Accept": "application/json"})
    try:
        with urlopen_no_redirect(request, timeout=10) as response:
            payload = json.loads(response.read().decode() or "{}")
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"{purpose} discovery failed: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=f"{purpose} returned invalid JSON.")
    return payload


def discover_oauth_metadata(server_url: str) -> OAuthConfig:
    """Discover an MCP resource's OAuth authorization-server metadata.

    Returns a dict with keys mapped to the hub's ``oauthConfig`` shape:
    ``authorizationUrl``, ``tokenUrl``, and optionally ``registrationUrl``.
    """
    protected_resource = OAuthProtectedResourceMetadata.model_validate(
        fetch_oauth_metadata_document(
            well_known_metadata_url(server_url, "oauth-protected-resource"),
            "OAuth protected-resource metadata",
        )
    )
    authorization_servers = protected_resource.authorization_servers
    if not authorization_servers:
        raise HTTPException(
            status_code=502,
            detail="OAuth protected-resource metadata is missing authorization_servers.",
        )
    authorization_server = next(
        (
            value.strip()
            for value in authorization_servers
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    if not authorization_server:
        raise HTTPException(
            status_code=502,
            detail="OAuth protected-resource metadata has no usable authorization server.",
        )
    try:
        payload = OAuthAuthorizationServerMetadata.model_validate(
            fetch_oauth_metadata_document(
                well_known_metadata_url(
                    authorization_server, "oauth-authorization-server"
                ),
                "OAuth authorization-server metadata",
            )
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=502,
            detail="OAuth metadata is missing authorization_endpoint or token_endpoint.",
        ) from exc
    return OAuthConfig(
        authorizationUrl=payload.authorization_endpoint,
        tokenUrl=payload.token_endpoint,
        registrationUrl=payload.registration_endpoint,
        code_challenge_methods_supported=payload.code_challenge_methods_supported,
    )


def enrich_dynamic_oauth_config(
    connector: ManagedConnector, metadata: OAuthConfig
) -> ManagedConnector:
    """Merge discovered OAuth endpoints into a connector's oauthConfig."""
    oauth_config = connector.oauthConfig or OAuthConfig()
    updates: dict[str, Any] = {
        "authorizationUrl": metadata.authorizationUrl,
        "tokenUrl": metadata.tokenUrl,
        "dynamicOAuth": True,
    }
    if metadata.registrationUrl and not oauth_config.registrationUrl:
        updates["registrationUrl"] = metadata.registrationUrl
    if "S256" in (metadata.code_challenge_methods_supported or []):
        updates["pkce"] = True
    # Mark the connector as dynamic OAuth so downstream code (e.g.
    # save_managed_connector) knows not to require a client_id.
    return connector.model_copy(
        update={"oauthConfig": oauth_config.model_copy(update=updates)}
    )


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def oauth_expiry(seconds: int = 600) -> str:
    return (
        (datetime.now(UTC) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


def split_oauth_scopes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part for part in re.split(r"[ ,]+", value) if part]
    return []


def join_oauth_scopes(scopes: list[str], separator: str | None = None) -> str:
    return ("," if separator == "comma" else " ").join(scopes)


_PREVIEW_PROXY_STATE_PREFIX = "oh_preview_v1"


def _https_origin(value: str) -> str | None:
    """Return a normalized HTTPS origin, rejecting non-origin URLs."""
    parsed = urllib.parse.urlparse(value.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"https://{parsed.hostname}"


def _site_origin(value: str) -> str:
    normalized = normalize_public_base_url(value)
    parsed = urllib.parse.urlsplit(normalized)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _configured_proxy_origin() -> str | None:
    raw = (os.getenv("INTHUB_OAUTH_REDIRECT_PROXY_URL") or "").strip()
    if not raw:
        return None
    origin = _https_origin(raw)
    if not origin:
        raise HTTPException(
            status_code=500,
            detail=(
                "INTHUB_OAUTH_REDIRECT_PROXY_URL must be an HTTPS origin "
                "without credentials, a custom port, path, query, or fragment."
            ),
        )
    return origin


def configured_public_base_url() -> str:
    """Return the configured Hub site URL, honoring one scheduled legacy key."""
    configured = get_default_config().public_base_url
    if configured:
        return configured
    legacy = (os.getenv("AUTH_URL") or "").strip()
    if legacy:
        warn_deprecated(
            "AUTH_URL",
            deprecated_in="0.7.1",
            removed_in="0.12.0",
            replacement="INTHUB_PUBLIC_BASE_URL",
            stacklevel=3,
        )
        try:
            return normalize_public_base_url(legacy)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"AUTH_URL is invalid: {exc}",
            ) from exc
    raise HTTPException(
        status_code=500,
        detail=(
            "OAuth callback URL is not configured. Set INTHUB_PUBLIC_BASE_URL "
            "to the externally visible Integrations Hub site URL."
        ),
    )


def oauth_callback_base_url() -> str:
    public_base_url = configured_public_base_url()
    origin = _configured_proxy_origin() or _site_origin(public_base_url)
    return f"{origin}{public_api_path('/api/oauth')}"


def oauth_state_with_preview_target() -> str:
    """Create OAuth state, carrying the safe return origin for a stable proxy.

    Preview deployments must register a stable OAuth callback URL, but their
    state records live in preview-local databases.  When both proxy and target
    URLs are configured, embed the configured preview origin in the otherwise
    opaque state so the stable callback can route the provider response back to
    the preview that created it.  The stable deployment validates the target
    against its explicit hostname allowlist before redirecting.
    """
    state = secrets.token_urlsafe(24)
    proxy = _configured_proxy_origin()
    if not proxy:
        return state
    target = _https_origin(_site_origin(configured_public_base_url()))
    if not target:
        raise HTTPException(
            status_code=500,
            detail=(
                "INTHUB_PUBLIC_BASE_URL must use HTTPS when "
                "INTHUB_OAUTH_REDIRECT_PROXY_URL is configured."
            ),
        )
    if proxy == target:
        return state
    return f"{_PREVIEW_PROXY_STATE_PREFIX}.{b64url(target.encode())}.{state}"


def oauth_proxy_callback_target(request: Request, state: str) -> str | None:
    """Return the validated preview origin for a stable OAuth callback.

    A state is routed only when the request reached the configured stable
    proxy origin and the encoded target hostname fully matches
    ``INTHUB_OAUTH_REDIRECT_PROXY_ALLOWED_TARGET_HOST_REGEX``.  A malformed or
    untrusted state deliberately falls through to normal callback handling,
    where it cannot consume a preview's OAuth state.
    """
    proxy = _configured_proxy_origin()
    pattern = (
        os.getenv("INTHUB_OAUTH_REDIRECT_PROXY_ALLOWED_TARGET_HOST_REGEX") or ""
    ).strip()
    if not proxy or not pattern:
        return None
    request_origin = _https_origin(str(request.base_url))
    if request_origin != proxy:
        return None

    prefix, separator, remainder = state.partition(".")
    if prefix != _PREVIEW_PROXY_STATE_PREFIX or not separator:
        return None
    encoded_target, separator, _opaque_state = remainder.partition(".")
    if not encoded_target or not separator:
        return None
    try:
        padded_target = encoded_target + "=" * (-len(encoded_target) % 4)
        target_value = base64.urlsafe_b64decode(padded_target).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    target = _https_origin(target_value)
    if not target:
        return None
    target_host = urllib.parse.urlparse(target).hostname
    try:
        allowed = bool(target_host and re.fullmatch(pattern, target_host))
    except re.error:
        return None
    return target if allowed else None


def requested_oauth_scopes(oauth_config: OAuthConfig) -> list[str]:
    scopes = split_oauth_scopes(oauth_config.scopes or [])
    optional = split_oauth_scopes(oauth_config.optionalScopes or [])
    optional.extend(
        split_oauth_scopes(
            (oauth_config.additionalAuthorizationParams or {}).get("optional_scope")
        )
    )
    seen: set[str] = set()
    result: list[str] = []
    for scope in [*scopes, *optional]:
        if scope not in seen:
            seen.add(scope)
            result.append(scope)
    return result


def token_request_body(
    connector: ManagedConnector, client: dict[str, str], values: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    oauth_config = connector.oauthConfig or OAuthConfig()
    auth_mode = oauth_config.clientAuthentication
    body = dict(values)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if auth_mode == "body":
        body["client_id"] = client["clientId"]
        body["client_secret"] = client.get("clientSecret", "")
    elif auth_mode == "none":
        body["client_id"] = client["clientId"]
    else:
        token = base64.b64encode(
            f"{client['clientId']}:{client.get('clientSecret', '')}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"
    for key, value in (oauth_config.additionalTokenParams or {}).items():
        body[str(key)] = str(value)
    return body, headers


def fetch_oauth_token(
    connector: ManagedConnector, body: dict[str, str], headers: dict[str, str]
) -> OAuthTokenResponse:
    oauth_config = connector.oauthConfig
    if not oauth_config or not oauth_config.tokenUrl:
        raise HTTPException(
            status_code=500, detail="OAuth token URL is not configured."
        )
    token_url = validate_external_url(
        oauth_config.tokenUrl, purpose="OAuth token endpoint"
    )
    request = urllib.request.Request(
        token_url,
        data=urllib.parse.urlencode(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen_no_redirect(request, timeout=10) as response:
            payload = json.loads(response.read().decode() or "{}")
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"OAuth token exchange failed: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502, detail="OAuth token exchange returned an invalid response."
        )
    try:
        return OAuthTokenResponse.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=502,
            detail="OAuth token exchange did not return a valid access token.",
        ) from exc


def value_at_path(payload: Any, path: Any) -> Any:
    if not isinstance(path, str) or not path:
        return None
    value = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def access_token_claims(access_token: Any) -> dict[str, Any]:
    if not isinstance(access_token, str):
        return {}
    parts = access_token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def fetch_oauth_identity(endpoint: str, access_token: str) -> Any:
    url = validate_external_url(endpoint, purpose="OAuth identity endpoint")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    try:
        with urlopen_no_redirect(request, timeout=10) as response:
            return json.loads(response.read().decode() or "{}")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="OAuth identity lookup failed.",
        ) from exc


def oauth_identity_payload(
    connector: ManagedConnector, payload: OAuthTokenResponse
) -> dict[str, Any]:
    model = connector.connectionModel
    mapping = model.identityMapping if model else None
    if not mapping:
        return {}
    source = mapping.source
    if source == "oauth_token_response":
        return payload.model_dump(exclude_none=True)
    if source == "access_token_claims":
        return access_token_claims(payload.access_token)
    if source == "identity_api" and mapping.endpoint:
        result = fetch_oauth_identity(mapping.endpoint, payload.access_token)
        return result if isinstance(result, dict) else {}
    return {}


def oauth_metadata(
    connector: ManagedConnector,
    payload: OAuthTokenResponse,
    existing: OAuthConnection | None = None,
) -> OAuthConnectionMetadata:
    existing_values = existing.model_dump(exclude_none=True) if existing else {}
    expires_at = None
    if isinstance(payload.expires_in, int):
        expires_at = oauth_expiry(payload.expires_in)
    scopes = (
        split_oauth_scopes(payload.scope)
        or split_oauth_scopes(existing_values.get("scopes"))
        or requested_oauth_scopes(connector.oauthConfig or OAuthConfig())
    )
    metadata = dict(existing_values.get("metadata") or {})
    model = connector.connectionModel
    mapping = model.identityMapping if model else None
    identity = oauth_identity_payload(connector, payload)
    external_account_id = optional_string(
        value_at_path(identity, mapping.externalPrincipalIdPath) if mapping else None
    )
    external_tenant_id = optional_string(
        value_at_path(identity, mapping.externalTenantIdPath) if mapping else None
    )
    external_resource_id = optional_string(
        value_at_path(identity, mapping.externalResourceIdPath) if mapping else None
    )
    resource_name = optional_string(
        value_at_path(identity, mapping.resourceNamePath) if mapping else None
    )
    resource_url = optional_string(
        value_at_path(identity, mapping.resourceUrlPath) if mapping else None
    )
    for key, value in {
        "externalTenantId": external_tenant_id,
        "externalResourceId": external_resource_id,
        "resourceType": model.resourceType if model else None,
        "resourceName": resource_name,
        "resourceUrl": resource_url,
        "principalType": model.principalType if model else None,
        "credentialScope": model.credentialScope if model else None,
    }.items():
        if value is not None:
            metadata[key] = value
    return OAuthConnectionMetadata(
        tokenType=payload.token_type or existing_values.get("tokenType"),
        scopes=scopes,
        expiresAt=expires_at,
        externalAccountId=external_account_id
        or payload.externalAccountId
        or existing_values.get("externalAccountId"),
        externalWorkspaceId=external_resource_id
        or payload.externalWorkspaceId
        or existing_values.get("externalWorkspaceId"),
        displayName=resource_name
        or payload.displayName
        or existing_values.get("displayName"),
        metadata=metadata,
    )


def oauth_connection_resources(
    connector: ManagedConnector,
    payload: OAuthTokenResponse,
    connection_metadata: OAuthConnectionMetadata,
) -> list[ConnectionResourceInput]:
    model = connector.connectionModel
    resource_type = model.resourceType if model else "resource"
    discovery = model.resourceDiscovery if model else None
    if discovery:
        discovered = fetch_oauth_identity(discovery.endpoint, payload.access_token)
        items = value_at_path(discovered, discovery.itemsPath)
        if not discovery.itemsPath:
            items = discovered
        if not isinstance(items, list):
            raise HTTPException(
                status_code=502,
                detail="OAuth resource discovery returned an invalid resource list.",
            )
        return [
            ConnectionResourceInput(
                resourceType=resource_type,
                externalResourceId=str(resource_id),
                displayName=optional_string(
                    value_at_path(item, discovery.resourceNamePath)
                ),
                externalUrl=optional_string(
                    value_at_path(item, discovery.resourceUrlPath)
                ),
            )
            for item in items
            if isinstance(item, dict)
            and (resource_id := value_at_path(item, discovery.externalResourceIdPath))
            is not None
        ]
    metadata = connection_metadata.metadata
    external_resource_id = (
        metadata.get("externalResourceId") or connection_metadata.externalWorkspaceId
    )
    if not external_resource_id:
        return []
    return [
        ConnectionResourceInput(
            resourceType=resource_type,
            externalResourceId=str(external_resource_id),
            displayName=optional_string(metadata.get("resourceName"))
            or connection_metadata.displayName,
            externalUrl=optional_string(metadata.get("resourceUrl")),
        )
    ]


def is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        value = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return value <= datetime.now(UTC)


def configured_oauth_callback_url(provider: str) -> str:
    return f"{oauth_callback_base_url()}/{provider}/callback"


def register_dynamic_oauth_client(connector: ManagedConnector) -> ManagedConnector:
    oauth_config = connector.oauthConfig or OAuthConfig()
    if oauth_config.clientAuthentication != "none" or not oauth_config.registrationUrl:
        return connector
    callback_url = configured_oauth_callback_url(connector.slug)
    data = json.dumps(
        {
            "client_name": f"OpenHands Integrations Hub ({connector.name})",
            "redirect_uris": [callback_url],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": oauth_config.clientAuthentication,
        }
    ).encode()
    registration_url = validate_external_url(
        oauth_config.registrationUrl, purpose="OAuth registration endpoint"
    )
    request = urllib.request.Request(
        registration_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen_no_redirect(request, timeout=10) as response:
            payload = json.loads(response.read().decode() or "{}")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{connector.name} OAuth client registration failed: {exc}",
        ) from exc
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(
            status_code=502,
            detail=f"{connector.name} OAuth client registration did not return a client_id.",
        )
    client_secret = str(payload.get("client_secret") or "").strip() or None
    return connector.model_copy(
        update={
            "oauthClientId": client_id,
            "oauthClientSecret": client_secret,
            "oauthConfigured": True,
        }
    )


def ensure_managed_oauth_client(connector: ManagedConnector) -> ManagedConnector:
    if connector.authStrategy != "oauth2":
        return connector
    if not connector.oauthConfig:
        raise HTTPException(
            status_code=400, detail="OAuth-managed connectors require an oauthConfig."
        )
    if connector.oauthClientId:
        if (
            connector.oauthConfig.clientAuthentication != "none"
            and not connector.oauthClientSecret
        ):
            raise HTTPException(
                status_code=400,
                detail="New OAuth-managed connectors require both oauthClientId and oauthClientSecret.",
            )
        return connector.model_copy(update={"oauthConfigured": True})
    existing_connector = repository.get_managed_connector(connector.slug)
    existing = (
        repository.read_oauth_client(existing_connector) if existing_connector else None
    )
    if existing:
        return connector.model_copy(
            update={
                "oauthClientId": existing["clientId"],
                "oauthClientSecret": existing.get("clientSecret"),
                "oauthConfigured": True,
            }
        )
    # Dynamic OAuth: the MCP server advertises its own endpoints via
    # .well-known/oauth-authorization-server. Discover them, optionally
    # register a client (RFC 7591) if the server exposes a registration
    # endpoint, then mark the connector as configured. No admin-supplied
    # client credentials or catalog authorizationUrl are needed.
    if is_dynamic_oauth(connector) and connector.serverUrl:
        metadata = discover_oauth_metadata(str(connector.serverUrl))
        enriched = enrich_dynamic_oauth_config(connector, metadata)
        if (
            enriched.oauthConfig
            and enriched.oauthConfig.clientAuthentication == "none"
            and enriched.oauthConfig.registrationUrl
        ):
            return register_dynamic_oauth_client(enriched)
        return enriched.model_copy(update={"oauthConfigured": True})
    if (
        connector.oauthConfig.clientAuthentication == "none"
        and connector.oauthConfig.registrationUrl
    ):
        return register_dynamic_oauth_client(connector)
    if existing_connector:
        # The connector already exists in the database (e.g. its OAuth client
        # credentials were stored previously or it was registered via a path
        # that did not encrypt them). Allow saving other changes (tool access
        # modes, server URL, etc.) without forcing the admin to re-enter OAuth
        # client credentials on every save.
        return connector.model_copy(
            update={"oauthConfigured": existing_connector.oauthConfigured}
        )
    raise HTTPException(
        status_code=400,
        detail="New OAuth-managed connectors require OAuth client credentials.",
    )


def start_oauth_redirect(
    request: Request, provider: str, owner: str, *, response_as_json: bool = False
) -> RedirectResponse | dict[str, str]:
    connector = get_managed_connector(provider)
    if not connector or not connector.oauthConfig:
        raise HTTPException(
            status_code=404, detail=f"Unknown OAuth provider '{provider}'."
        )
    oauth_config = connector.oauthConfig
    # Dynamic OAuth connectors have no catalog authorizationUrl; discover the
    # server's OAuth metadata at runtime so we can redirect the user to the
    # right authorization endpoint. The discovered tokenUrl is stashed in the
    # oauth_state's callback_url field so the callback can exchange the code
    # without re-discovering.
    discovered_token_url: str | None = None
    if is_dynamic_oauth(connector) and connector.serverUrl:
        metadata = discover_oauth_metadata(str(connector.serverUrl))
        connector = enrich_dynamic_oauth_config(connector, metadata)
        oauth_config = connector.oauthConfig or OAuthConfig()
        discovered_token_url = metadata.tokenUrl
    # For dynamic OAuth with a discovered registration endpoint and public
    # clients (no secret), re-register on every start so the redirect_uri
    # always matches the current hub configuration.  When the hub's public
    # URL or api_root_path changes after an initial registration, the
    # provider still has the old redirect_uri on file and rejects the
    # authorization request with "redirect URI provided for the application
    # was invalid."  Re-registering refreshes it.
    if (
        is_dynamic_oauth(connector)
        and oauth_config.clientAuthentication == "none"
        and oauth_config.registrationUrl
    ):
        connector = register_dynamic_oauth_client(connector)
        repository.save_managed_connector(connector)
    client = repository.read_oauth_client(connector)
    if not client:
        raise HTTPException(
            status_code=500,
            detail=f"{connector.name} is missing stored OAuth client credentials.",
        )
    state = oauth_state_with_preview_target()
    verifier = secrets.token_urlsafe(48) if oauth_config.pkce else None
    raw_redirect_to = (
        request.query_params.get("redirectTo")
        or f"/integrations?showIntegrationWizard=1&managedConnector={provider}"
    )
    # Defense-in-depth: reject external/protocol-relative redirect targets at
    # ingest so obviously-bad rows never enter oauth_states. The callback
    # also validates before issuing the 302.
    redirect_to = public_ui_path(
        raw_redirect_to
        if raw_redirect_to.startswith("/") and not raw_redirect_to.startswith("//")
        else f"/integrations?showIntegrationWizard=1&managedConnector={provider}"
    )
    integration_key = request.query_params.get("integrationKey") or provider
    redirect_uri = configured_oauth_callback_url(provider)
    query = {
        "response_type": "code",
        "client_id": client["clientId"],
        "redirect_uri": redirect_uri,
        "state": state,
    }
    scope_separator = oauth_config.scopeSeparator
    scopes = split_oauth_scopes(oauth_config.scopes or [])
    if scopes:
        query["scope"] = join_oauth_scopes(scopes, scope_separator)
    optional_scopes = split_oauth_scopes(oauth_config.optionalScopes or [])
    optional_scopes.extend(
        split_oauth_scopes(
            (oauth_config.additionalAuthorizationParams or {}).get("optional_scope")
        )
    )
    if optional_scopes:
        query["optional_scope"] = join_oauth_scopes(optional_scopes, scope_separator)
    if verifier:
        query["code_challenge"] = b64url(hashlib.sha256(verifier.encode()).digest())
        query["code_challenge_method"] = "S256"
    for key, value in (oauth_config.additionalAuthorizationParams or {}).items():
        if key == "scope" and scopes:
            continue
        if key == "optional_scope" and optional_scopes:
            continue
        query[str(key)] = str(value)
    repository.save_oauth_state(
        state,
        owner,
        integration_key,
        provider,
        verifier,
        redirect_to,
        redirect_uri,
        discovered_token_url,
        oauth_expiry(),
    )
    redirect_url = f"{oauth_config.authorizationUrl}?{urllib.parse.urlencode(query)}"
    if response_as_json:
        return {"redirectUrl": redirect_url}
    return RedirectResponse(redirect_url)


def exchange_oauth_code(provider: str, state: str, code: str) -> dict[str, Any]:
    record = repository.consume_oauth_state(state, provider)
    if not record:
        raise HTTPException(
            status_code=400, detail="OAuth state not found or already used."
        )
    connector = get_managed_connector(provider)
    if not connector or not connector.oauthConfig:
        raise HTTPException(
            status_code=404, detail=f"Unknown OAuth provider '{provider}'."
        )
    # For dynamic OAuth, the token endpoint was discovered at start time and
    # stashed in the state's callback_url field. Enrich the connector so
    # fetch_oauth_token can use it.
    discovered_token_url = record.callbackUrl or ""
    if discovered_token_url and not connector.oauthConfig.tokenUrl:
        connector = enrich_dynamic_oauth_config(
            connector,
            OAuthConfig(authorizationUrl="", tokenUrl=str(discovered_token_url)),
        )
    client = repository.read_oauth_client(connector)
    if not client:
        raise HTTPException(
            status_code=500,
            detail=f"{connector.name} is missing stored OAuth client credentials.",
        )
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": record.redirectUri,
    }
    if record.codeVerifier:
        body["code_verifier"] = record.codeVerifier
    body, headers = token_request_body(connector, client, body)
    payload = OAuthTokenResponse.model_validate(
        fetch_oauth_token(connector, body, headers)
    )
    access_token = payload.access_token
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail="OAuth token exchange did not return an access token.",
        )
    connection_metadata = oauth_metadata(connector, payload)
    resources = oauth_connection_resources(connector, payload, connection_metadata)
    connection = repository.save_connection(
        record.ownerId,
        record.integrationKey,
        provider,
        "oauth2",
        {
            "accessToken": access_token,
            **(
                {"refreshToken": payload.refresh_token} if payload.refresh_token else {}
            ),
        },
        connection_metadata,
        resources,
    ) | {
        "ownerId": record.ownerId,
    }
    # Preserve the wizard return target (`redirect_to`) so the callback can
    # send the user back into the integration wizard they started OAuth from,
    # instead of dropping them on /integrations with the wizard closed.
    connection["redirect_to"] = record.redirectTo
    return connection


def consume_oauth_redirect_target(provider: str, state: str) -> str:
    """Return the saved `redirect_to` for an OAuth state, consuming the row.

    Used by the OAuth callback error branch, where there is no code to exchange
    but we still want to send the user back to the wizard (or other origin)
    that kicked off the flow instead of a bare /integrations URL. The state is
    consumed so it cannot be replayed.
    """
    record = repository.consume_oauth_state(state, provider)
    return record.redirectTo if record else ""


def build_oauth_callback_redirect(
    base_redirect_to: str,
    *,
    oauth_status: str,
    oauth_provider: str,
    oauth_error: str | None = None,
    integration_key: str | None = None,
) -> str:
    """Merge OAuth status params onto the saved return target.

    The wizard starts OAuth with a `redirectTo` like
    `/integrations?showIntegrationWizard=1&managedConnector=<slug>`. After the
    provider redirects back, we append `oauth_status` / `oauth_provider` /
    `oauth_error` / `integrationKey` so the dashboard can show the result
    banner while keeping the wizard open. Falls back to `/integrations` when
    no return target was saved.

    Security: `base_redirect_to` originates from an untrusted query parameter
    (`redirectTo` on `/api/oauth/{provider}/start`). Validate it is a
    same-origin relative path to prevent open-redirect attacks — reject
    absolute URLs (`https://evil.example/...`) and protocol-relative URLs
    (`//evil.example/...`).
    """
    target = base_redirect_to.strip()
    if not target or not target.startswith("/") or target.startswith("//"):
        target = "/integrations"
    target = public_ui_path(target)
    parsed = urllib.parse.urlparse(target)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    # Remove any existing integrationKey so we don't duplicate it when the
    # callback appends the resolved key.
    params = [(k, v) for k, v in params if k != "integrationKey"]
    params.append(("oauth_status", oauth_status))
    params.append(("oauth_provider", oauth_provider))
    if oauth_error:
        params.append(("oauth_error", oauth_error))
    if integration_key:
        params.append(("integrationKey", integration_key))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(params))
    )


def refresh_oauth_connection(
    record: OAuthConnection | dict[str, Any],
) -> OAuthConnection:
    record = OAuthConnection.model_validate(record)
    connector = get_managed_connector(record.provider)
    if not connector or not connector.oauthConfig:
        raise HTTPException(
            status_code=500,
            detail=f"OAuth provider '{record.provider}' is not configured.",
        )
    # Dynamic OAuth: the connector may be a catalog default without a
    # persisted tokenUrl. Re-discover so the refresh can hit the right
    # endpoint.
    if is_dynamic_oauth(connector) and connector.serverUrl:
        connector = enrich_dynamic_oauth_config(
            connector, discover_oauth_metadata(str(connector.serverUrl))
        )
    client = repository.read_oauth_client(connector)
    if not client:
        raise HTTPException(
            status_code=500,
            detail=f"{connector.name} is missing stored OAuth client credentials.",
        )
    refresh_token = str(record.credentials.refreshToken or "").strip()
    if not refresh_token:
        raise HTTPException(
            status_code=403,
            detail=f"Reconnect {record.provider} to refresh the expired access token.",
        )
    body, headers = token_request_body(
        connector,
        client,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    payload = OAuthTokenResponse.model_validate(
        fetch_oauth_token(connector, body, headers)
    )
    access_token = payload.access_token
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail="OAuth token refresh did not return an access token.",
        )
    connection_metadata = oauth_metadata(connector, payload, record)
    resources = oauth_connection_resources(connector, payload, connection_metadata)
    saved = repository.save_connection(
        record.ownerId,
        record.integrationKey,
        record.provider,
        "oauth2",
        {
            "accessToken": access_token,
            "refreshToken": payload.refresh_token or refresh_token,
        },
        connection_metadata,
        resources,
    )
    return OAuthConnection.model_validate(
        {
            "ownerId": record.ownerId,
            "credentials": {
                "accessToken": access_token,
                "refreshToken": payload.refresh_token or refresh_token,
            },
            **saved,
        }
    )


def credentials_for(owner: str, integration: IntegrationSpec) -> dict[str, Any]:
    """Return provider credentials, refreshing expired OAuth tokens as a side effect."""
    if integration.authStrategy == "oauth2":
        configured_connection_id = integration.config.get("connectionId")
        stored_record = (
            repository.get_connection_by_id(owner, str(configured_connection_id))
            if configured_connection_id
            else repository.get_connection(owner, integration.key)
        )
        if not stored_record:
            raise HTTPException(
                status_code=403,
                detail=f"Connect {integration.name} for this user before invoking '{integration.key}'.",
            )
        record = OAuthConnection.model_validate(stored_record)
        if is_expired(record.expiresAt):
            record = refresh_oauth_connection(record)
        return record.credentials.model_dump(exclude_none=True)
    return integration.credentials
