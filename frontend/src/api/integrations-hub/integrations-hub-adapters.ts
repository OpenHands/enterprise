import type {
  HubApiAccessRequest,
  HubApiAuthStrategy,
  HubApiIntegrationRequest,
  HubApiIntegrationSpec,
  HubApiKeyStatus,
  HubApiManagedConnector,
  HubApiOverview,
  HubApiOverviewUser,
  HubApiPermissionProfile,
  HubApiToolAccessMode,
  HubApiToolSpec,
} from "#/api/integrations-hub/integrations-hub.types";
import type {
  HubApproval,
  HubApprovalStatus,
  HubAuthStrategy,
  HubIntegration,
  HubPermissionProfile,
  HubTool,
  HubToolAccessMode,
  HubUserRequest,
  HubApiKey,
  HubOverviewUser,
  HubDuplicateGroup,
} from "#/types/integrations-hub";

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(String).filter(Boolean);
  }
  if (value instanceof Set) {
    return [...value].map(String).filter(Boolean);
  }
  return [];
}

function mapAuthStrategy(value?: HubApiAuthStrategy | string): HubAuthStrategy {
  if (value === "api_key" || value === "none") {
    return value;
  }
  return "oauth2";
}

function mapToolAccessMode(
  value?: HubApiToolAccessMode | string,
): HubToolAccessMode {
  if (value === "disabled") {
    return "disabled";
  }
  if (value === "approval_required" || value === "approval") {
    return "approval";
  }
  return "enabled";
}

function mapMaxAccessMode(
  value?: HubApiToolAccessMode | string | null,
): HubToolAccessMode | undefined {
  if (!value) {
    return undefined;
  }
  return mapToolAccessMode(value);
}

function toolsFromSpec(
  tools: HubApiIntegrationSpec["tools"] | HubApiManagedConnector["tools"],
): HubTool[] {
  if (!tools) {
    return [];
  }
  const list = Array.isArray(tools) ? tools : Object.values(tools);
  return list.map((tool: HubApiToolSpec) => ({
    name: tool.name,
    description: tool.description ?? "",
    accessMode: mapToolAccessMode(tool.accessMode),
    maxAccessMode: mapMaxAccessMode(tool.maxAccessMode),
    defaultScopes: tool.defaultScopes ?? [],
    lastUsedAt: tool.lastInvokedAt ?? undefined,
  }));
}

export function integrationSpecToHubIntegration(
  spec: HubApiIntegrationSpec,
  options?: { connected?: boolean },
): HubIntegration {
  const tools = toolsFromSpec(spec.tools);
  return {
    slug: spec.key,
    name: spec.name,
    description:
      typeof spec.config?.description === "string"
        ? spec.config.description
        : "",
    connected: options?.connected ?? true,
    enabled: spec.enabled !== false,
    authStrategy: mapAuthStrategy(spec.authStrategy),
    toolCount: tools.length,
    provider: String(spec.provider ?? "Custom"),
    kind: String(spec.kind ?? "Custom"),
    tools,
  };
}

export function managedConnectorToHubIntegration(
  connector: HubApiManagedConnector,
  installed?: HubApiIntegrationSpec | null,
): HubIntegration {
  const tools = installed
    ? toolsFromSpec(installed.tools)
    : toolsFromSpec(connector.tools);
  return {
    slug: connector.slug,
    name: connector.name,
    description: connector.description ?? "",
    connected: Boolean(installed),
    enabled: installed ? installed.enabled !== false : false,
    authStrategy: mapAuthStrategy(
      installed?.authStrategy ?? connector.authStrategy,
    ),
    toolCount: tools.length || (connector.tools?.length ?? 0),
    provider: connector.provider === "http" ? "HTTP" : "MCP",
    kind: "Catalog",
    tools,
    logoUrl: connector.logoUrl ?? undefined,
    categories: connector.categories,
    docsUrl: connector.docsUrl ?? undefined,
    connectorProvider: connector.provider,
    serverUrl: connector.serverUrl ?? undefined,
    apiBaseUrl: connector.apiBaseUrl ?? undefined,
    openApiUrl: connector.openApiUrl ?? undefined,
  };
}

export function accessRequestToHubApproval(
  request: HubApiAccessRequest,
  integrationName?: string,
): HubApproval {
  const status: HubApprovalStatus =
    request.status === "approved" || request.status === "denied"
      ? request.status
      : "pending";
  return {
    id: request.id,
    toolName: request.toolName,
    integrationKey: request.integrationKey,
    integrationName: integrationName ?? request.integrationKey,
    status,
    agentId: request.agentId ?? "",
    agentClass: request.agentClass ?? "",
    justification: request.justification,
    scopes: request.scopes ?? [],
    requestedMinutes: request.requestedMinutes ?? 240,
    createdAt: request.createdAt,
    decidedBy: request.decidedBy ?? undefined,
    decidedAt: request.decidedAt ?? undefined,
    conversationId: request.agentData?.conversationId,
  };
}

export function integrationRequestToHubUserRequest(
  request: HubApiIntegrationRequest,
): HubUserRequest {
  return {
    id: request.id,
    name: request.name,
    slug: request.slug ?? request.name.toLowerCase().replace(/\s+/g, "-"),
    requestedBy: request.requestedBy,
    notes: request.notes ?? "",
    description: request.description,
    docsUrl: request.docsUrl,
    source: request.source,
    createdAt: request.createdAt,
  };
}

export function permissionProfileToHub(
  profile: HubApiPermissionProfile,
  options?: { isDefault?: boolean },
): HubPermissionProfile {
  const snapshotIntegrations = profile.snapshot?.integrations ?? {};
  const toolCount = Object.values(snapshotIntegrations).reduce(
    (sum, entry) => sum + Object.keys(entry.tools ?? {}).length,
    0,
  );
  return {
    id: profile.id,
    name: profile.name?.trim() || "Untitled profile",
    summary: `${toolCount} tools configured`,
    updatedAt:
      profile.updatedAt ?? profile.createdAt ?? new Date().toISOString(),
    isDefault: options?.isDefault ?? profile.isDefault,
    agentApiKey: profile.agentApiKey ?? "",
    snapshot: {
      integrations: Object.fromEntries(
        Object.entries(snapshotIntegrations).map(([key, entry]) => [
          key,
          {
            enabled: entry.enabled !== false,
            tools: Object.fromEntries(
              Object.entries(entry.tools ?? {}).map(([tool, mode]) => [
                tool,
                mapToolAccessMode(mode),
              ]),
            ),
          },
        ]),
      ),
    },
  };
}

export function keyStatusToHubApiKey(
  status: HubApiKeyStatus,
  fallbackName: string,
): HubApiKey | null {
  const value = status.key ?? status.value;
  if (!value) {
    return null;
  }
  return {
    id: status.id ?? fallbackName,
    name: status.name ?? fallbackName,
    value,
  };
}

function overviewUserToHub(user: HubApiOverviewUser): HubOverviewUser {
  return {
    ownerId: user.ownerId,
    totalConnections: user.totalConnections,
    totalAccessRequests: user.totalAccessRequests,
    pendingAccessRequests: user.pendingAccessRequests,
    notifications: user.notifications,
    integrations: asStringArray(user.integrations),
    providers: asStringArray(user.providers),
    connections: (user.connections ?? []).map((connection) => ({
      integrationKey: String(connection.integrationKey ?? ""),
      provider: String(connection.provider ?? ""),
      displayName: String(
        connection.displayName ?? connection.integrationKey ?? "",
      ),
      externalAccountId: String(connection.externalAccountId ?? ""),
      authStrategy: mapAuthStrategy(
        connection.authStrategy as HubApiAuthStrategy | undefined,
      ),
      updatedAt: String(connection.updatedAt ?? ""),
    })),
    duplicateExternalAccounts: (user.duplicateExternalAccounts ?? []).map(
      (group) => ({
        provider: group.provider,
        externalAccountId: group.externalAccountId,
        ownerIds: group.ownerIds ?? [],
        displayNames: group.displayNames,
      }),
    ),
  };
}

export function overviewToHub(overview: HubApiOverview): {
  overviewUsers: HubOverviewUser[];
  duplicateGroups: HubDuplicateGroup[];
} {
  const overviewUsers = (overview.users ?? []).map(overviewUserToHub);
  const duplicateGroups =
    overview.duplicateGroups ??
    overviewUsers.flatMap((user) => user.duplicateExternalAccounts ?? []);
  return { overviewUsers, duplicateGroups };
}

export function hubAccessModeToApi(
  mode: HubToolAccessMode,
): HubApiToolAccessMode {
  if (mode === "disabled") {
    return "disabled";
  }
  if (mode === "approval") {
    return "approval_required";
  }
  return "enabled";
}
