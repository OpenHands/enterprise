from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from openhands_extensions import IntegrationConnectionModel
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool


AuthStrategy = Literal["none", "api_key", "bearer", "basic", "oauth2", "mcp_session"]
IntegrationKind = Literal["api", "mcp"]
IntegrationProvider = Literal["mcp", "mock", "http"]
ToolAccessMode = Literal["enabled", "disabled", "approval_required"]
AccessRequestStatus = Literal["pending", "approved", "rejected", "expired"]
IntegrationRequestStatus = Literal["pending", "added", "dismissed"]
HttpRequestContentType = Literal[
    "json", "form_urlencoded", "multipart_form_data", "text"
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OAuthConfig(BaseModel):
    """Hub runtime OAuth configuration, including dynamic-MCP metadata."""

    model_config = ConfigDict(extra="forbid")

    authorizationUrl: str | None = None
    tokenUrl: str | None = None
    scopes: list[str] | None = None
    optionalScopes: list[str] | None = None
    toolScopes: list[str] | None = None
    scopeSeparator: Literal["space", "comma"] | None = None
    pkce: bool | None = None
    clientAuthentication: Literal["basic", "body", "none"] | None = None
    registrationUrl: str | None = None
    additionalAuthorizationParams: dict[str, str] | None = None
    additionalTokenParams: dict[str, str] | None = None
    dynamicOAuth: bool | None = None
    code_challenge_methods_supported: list[str] | None = None

    def get(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        """Compatibility accessor while callers migrate from raw config maps."""
        return getattr(self, key, default)


class HttpRequestTemplate(BaseModel):
    """Validated request template for an HTTP managed connector tool."""

    model_config = ConfigDict(extra="forbid")

    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    query: dict[str, JsonValue] = Field(default_factory=dict)
    headers: dict[str, JsonValue] = Field(default_factory=dict)
    body: JsonValue | None = None
    contentType: HttpRequestContentType = "json"


class ConnectionDefaults(BaseModel):
    """Connection settings derived from a typed extensions catalog option."""

    provider: Literal["mcp", "http"]
    authStrategy: AuthStrategy
    authModes: list[AuthStrategy] | None = None
    credentialLabel: str | None = None
    credentialPlaceholder: str | None = None
    credentialHelp: str | None = None
    apiKeyHeaderName: str | None = None
    apiKeyOptional: bool | None = None
    apiBaseUrl: str | None = None
    serverUrl: str | None = None
    openApiUrl: str | None = None
    oauthConfig: OAuthConfig | None = None
    connectionModel: IntegrationConnectionModel | None = None


class OAuthTokenResponse(BaseModel):
    """Provider token response with vendor fields retained for compatibility."""

    model_config = ConfigDict(extra="allow")

    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    token_type: str | None = None
    expires_in: int | str | None = None
    scope: str | list[str] | None = None
    externalAccountId: str | None = None
    externalWorkspaceId: str | None = None
    displayName: str | None = None

    def get(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        return getattr(
            self,
            key,
            self.model_extra.get(key, default) if self.model_extra else default,
        )


class OAuthProtectedResourceMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    authorization_servers: list[str] = Field(default_factory=list)


class OAuthAuthorizationServerMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    authorization_endpoint: str = Field(min_length=1)
    token_endpoint: str = Field(min_length=1)
    registration_endpoint: str | None = None
    code_challenge_methods_supported: list[str] = Field(default_factory=list)


class OAuthConnectionMetadata(BaseModel):
    tokenType: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expiresAt: str | None = None
    externalAccountId: str | None = None
    externalWorkspaceId: str | None = None
    displayName: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class OAuthCredentials(BaseModel):
    accessToken: str
    refreshToken: str | None = None


class ConnectionResourceInput(BaseModel):
    resourceType: str
    externalResourceId: str
    displayName: str | None = None
    externalUrl: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ConnectionResource(ConnectionResourceInput):
    id: str
    connectionId: str
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)


class OAuthConnection(BaseModel):
    ownerId: str
    connectionId: str
    integrationKey: str
    provider: str
    authStrategy: Literal["oauth2"] = "oauth2"
    credentials: OAuthCredentials
    tokenType: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expiresAt: str | None = None
    externalAccountId: str | None = None
    externalWorkspaceId: str | None = None
    externalTenantId: str | None = None
    displayName: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    resources: list[ConnectionResource] = Field(default_factory=list)
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)


class OAuthState(BaseModel):
    state: str
    ownerId: str
    integrationKey: str
    provider: str
    codeVerifier: str | None = None
    redirectTo: str
    redirectUri: str
    callbackUrl: str | None = None
    expiresAt: str


class KeyStatus(BaseModel):
    keyPrefix: str
    keySuffix: str
    recoverable: bool = True
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)


class AgentApiKeyRecord(BaseModel):
    ownerId: str
    apiKey: str
    permissionProfileId: str | None = None
    permissionProfileName: str | None = None
    createdAt: str
    updatedAt: str


class TemporaryGrant(BaseModel):
    id: str
    ownerId: str
    agentId: str | None = None
    agentClass: str | None = None
    integrationKey: str
    toolName: str
    scopes: list[str] = Field(default_factory=list)
    grantedBy: str
    createdAt: str
    expiresAt: str


class ConnectionSummary(BaseModel):
    ownerId: str
    connectionId: str
    integrationKey: str
    provider: str
    authStrategy: AuthStrategy
    tokenType: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expiresAt: str | None = None
    externalAccountId: str | None = None
    externalWorkspaceId: str | None = None
    externalTenantId: str | None = None
    displayName: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    resources: list[ConnectionResource] = Field(default_factory=list)
    createdAt: str
    updatedAt: str


class ToolUsageSummary(BaseModel):
    integrationKey: str
    toolName: str
    lastInvokedAt: str
    lastInvocationOutcome: Literal["success", "denied", "error"]
    invocationCount: int = 0


class NotificationRecord(BaseModel):
    id: str
    channel: str
    recipient: str
    subject: str
    body: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    createdAt: str = Field(default_factory=now_iso)


class DuplicateExternalAccount(BaseModel):
    provider: str
    externalAccountId: str
    connections: list[ConnectionSummary] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)


class OwnerOverview(BaseModel):
    ownerId: str
    totalConnections: int = 0
    integrations: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    totalAccessRequests: int = 0
    pendingAccessRequests: int = 0
    notifications: int = 0
    duplicateExternalAccounts: list[DuplicateExternalAccount] = Field(
        default_factory=list
    )
    connections: list[ConnectionSummary] = Field(default_factory=list)


class AdminOverview(BaseModel):
    totalConnections: int
    connectedOwners: int
    managedConnectors: int
    totalAccessRequests: int
    pendingAccessRequests: int
    notifications: int
    duplicateExternalAccounts: list[DuplicateExternalAccount] = Field(
        default_factory=list
    )
    users: list[OwnerOverview] = Field(default_factory=list)


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    accessMode: ToolAccessMode = "enabled"
    maxAccessMode: ToolAccessMode | None = None
    fineGrainedPermissions: bool = True
    defaultScopes: list[str] = Field(default_factory=list)
    executionMode: Literal["provider", "mock"] = "provider"
    config: dict[str, Any] = Field(default_factory=dict)
    mockResponse: dict[str, Any] | None = None
    lastInvokedAt: str | None = None
    lastInvocationOutcome: Literal["success", "denied", "error"] | None = None
    invocationCount: int | None = None


class IntegrationSpec(BaseModel):
    key: str
    name: str
    kind: IntegrationKind
    provider: IntegrationProvider
    authStrategy: AuthStrategy
    enabled: bool = True
    fineGrainedPermissions: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, ToolSpec] = Field(default_factory=dict)

    def redacted(self) -> IntegrationSpec:
        return self.model_copy(update={"credentials": {}})


class AgentInvocationRequest(BaseModel):
    agentId: str | None = None
    agentClass: str | None = None
    scopes: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolAccessPatch(BaseModel):
    """The only mutable field on an installed tool's access endpoint."""

    model_config = ConfigDict(extra="forbid")

    accessMode: ToolAccessMode


class IntegrationToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Omitting enabled preserves the endpoint's existing toggle behavior.
    enabled: StrictBool | None = None


class IntegrationDiscoveryRequest(BaseModel):
    """Stable discovery envelope around provider-specific config and credentials."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["managed", "mcp"] | None = None
    key: str | None = None
    name: str | None = None
    managedConnectorSlug: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_managed(self) -> bool:
        return self.kind == "managed" or (
            self.kind is None
            and bool(
                self.managedConnectorSlug or self.config.get("managedConnectorSlug")
            )
        )


class DisableUnusedToolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thresholdValue: int = Field(default=30, gt=0)
    thresholdUnit: Literal["days", "weeks", "months"] = "days"


class IntegrationRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["catalog", "custom"]
    slug: str | None = None
    name: str = ""
    description: str = ""
    docsUrl: str = ""
    notes: str = ""


class IntegrationRequestRecord(BaseModel):
    id: str
    source: Literal["catalog", "custom"]
    slug: str | None = None
    name: str
    description: str = ""
    docsUrl: str = ""
    notes: str = ""
    requestedBy: str
    status: IntegrationRequestStatus = "pending"
    decidedBy: str | None = None
    decidedAt: str | None = None
    createdAt: str = Field(default_factory=now_iso)


class AccessRequestApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    durationMinutes: int | None = Field(default=None, gt=0)


class AdminConnectionFilters(BaseModel):
    """Validated filters shared by the admin connection route and repository."""

    ownerId: str | None = None
    provider: str | None = None
    integrationKey: str | None = None
    q: str | None = None
    limit: int = Field(default=100, ge=1)
    offset: int = Field(default=0, ge=0)
    admin: bool = True


class AccessRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    agentId: str | None = None
    agentClass: str | None = None
    integrationKey: str
    toolName: str
    scopes: list[str] = Field(default_factory=list)
    requestedMinutes: int = 240
    channels: list[str] = Field(default_factory=list)
    notificationTargets: dict[str, str] = Field(default_factory=dict)
    justification: str = ""
    agentData: dict[str, Any] = Field(default_factory=dict)
    createdAt: str = Field(default_factory=now_iso)
    status: AccessRequestStatus = "pending"
    decidedAt: str | None = None
    decidedBy: str | None = None


class PermissionProfileIntegrationSnapshot(BaseModel):
    enabled: bool = True
    tools: dict[str, ToolAccessMode] = Field(default_factory=dict)


class PermissionProfileSnapshot(BaseModel):
    integrations: dict[str, PermissionProfileIntegrationSnapshot] = Field(
        default_factory=dict
    )


class PermissionProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    sourceProfileId: str | None = None
    snapshot: PermissionProfileSnapshot | None = None


class PermissionProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: PermissionProfileSnapshot


class PermissionProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ownerId: str
    name: str | None = None
    snapshot: PermissionProfileSnapshot = Field(
        default_factory=PermissionProfileSnapshot
    )
    agentApiKey: str = ""
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)


class PermissionProfileApplyResult(BaseModel):
    profile: PermissionProfile
    appliedIntegrations: int
    appliedTools: int
    skippedIntegrations: list[str] = Field(default_factory=list)
    skippedTools: list[str] = Field(default_factory=list)


class ManagedConnectorTool(BaseModel):
    name: str
    description: str = ""
    defaultScopes: list[str] = Field(default_factory=list)
    accessMode: ToolAccessMode = "enabled"
    request: HttpRequestTemplate | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ManagedConnector(BaseModel):
    slug: str
    name: str
    description: str
    logoUrl: str | None = None
    iconBg: str | None = None
    iconColor: str | None = None
    appUrl: str | None = None
    docsUrl: str | None = None
    categories: list[str] = Field(default_factory=list)
    authModes: list[AuthStrategy] = Field(default_factory=list)
    authStrategy: AuthStrategy
    provider: Literal["mcp", "http"] = "mcp"
    oauthConfigured: bool = False
    credentialLabel: str = "API key"
    credentialPlaceholder: str = "Paste API key"
    credentialHelp: str = "Credential used by the connector."
    apiKeyHeaderName: str | None = None
    apiBaseUrl: str | None = None
    serverUrl: str | None = None
    openApiUrl: str | None = None
    oauthConfig: OAuthConfig | None = None
    connectionModel: IntegrationConnectionModel | None = None
    oauthClientId: str | None = None
    oauthClientSecret: str | None = None
    tools: list[ManagedConnectorTool] = Field(default_factory=list)
    enabled: bool = True
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)

    def option(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "logoUrl": self.logoUrl,
            "iconBg": self.iconBg,
            "iconColor": self.iconColor,
            "appUrl": self.appUrl,
            "toolCount": len(self.tools),
            "categories": self.categories,
            "authModes": self.authModes or [self.authStrategy],
            "authStrategy": self.authStrategy,
            "oauthConfigured": self.oauthConfigured,
            "credentialLabel": self.credentialLabel,
            "credentialPlaceholder": self.credentialPlaceholder,
            "credentialHelp": self.credentialHelp,
            "apiKeyHeaderName": self.apiKeyHeaderName,
            "connectionModel": self.connectionModel,
        }

    def response(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"oauthClientId", "oauthClientSecret"})
        data["oauthConfigured"] = self.oauthConfigured
        data["oauthClientStatus"] = (
            "decryptable" if self.oauthConfigured else "not_applicable"
        )
        return data
