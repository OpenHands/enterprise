import { openHands } from "#/api/open-hands-axios";
import type {
  HubApiAccessRequest,
  HubApiIntegrationRequest,
  HubApiIntegrationSpec,
  HubApiKeyStatus,
  HubApiManagedConnector,
  HubApiOverview,
  HubApiPermissionProfile,
  HubApiToolAccessMode,
} from "#/api/integrations-hub/integrations-hub.types";
import type {
  HubCustomMcpInput,
  HubIntegrationRequestPayload,
  HubPermissionProfileSnapshot,
} from "#/types/integrations-hub";

const HUB_BASE = "/api/integrations-hub";

async function hubGet<T>(path: string, params?: Record<string, string>) {
  const { data } = await openHands.get<T>(`${HUB_BASE}${path}`, { params });
  return data;
}

async function hubPost<T>(path: string, body?: unknown) {
  const { data } = await openHands.post<T>(`${HUB_BASE}${path}`, body ?? {});
  return data;
}

async function hubPatch<T>(path: string, body?: unknown) {
  const { data } = await openHands.patch<T>(`${HUB_BASE}${path}`, body ?? {});
  return data;
}

async function hubDelete<T>(path: string) {
  const { data } = await openHands.delete<T>(`${HUB_BASE}${path}`);
  return data;
}

export const integrationsHubService = {
  listIntegrations: () => hubGet<HubApiIntegrationSpec[]>("/integrations"),

  listCatalog: (filter: "all" | "registerable" | "enabled" = "enabled") =>
    hubGet<HubApiManagedConnector[] | HubApiIntegrationSpec[]>(
      "/integrations",
      { filter },
    ),

  listApprovals: () => hubGet<HubApiAccessRequest[]>("/case-by-case-approvals"),

  listUserRequests: () =>
    hubGet<HubApiIntegrationRequest[]>("/admin/integration-requests"),

  listPermissionProfiles: () =>
    hubGet<HubApiPermissionProfile[]>("/permission-profiles"),

  getOverview: () => hubGet<HubApiOverview>("/admin/overview"),

  getUserKey: () => hubGet<HubApiKeyStatus>("/user/key"),

  getUserAgentKey: () => hubGet<HubApiKeyStatus>("/user/agent-key"),

  listAdminConnectors: () =>
    hubGet<HubApiManagedConnector[]>("/admin/modify-integrations"),

  createIntegration: (body: HubApiIntegrationSpec) =>
    hubPost<HubApiIntegrationSpec>("/integrations", body),

  deleteIntegration: (integrationKey: string) =>
    hubDelete<{ deleted: boolean }>(`/integrations/${integrationKey}`),

  toggleIntegration: (integrationKey: string, enabled?: boolean) =>
    hubPost<HubApiIntegrationSpec>(`/integrations/${integrationKey}/toggle`, {
      enabled,
    }),

  patchToolAccess: (
    integrationKey: string,
    toolName: string,
    accessMode: HubApiToolAccessMode,
  ) =>
    hubPatch<HubApiIntegrationSpec>(
      `/integrations/${integrationKey}/tools/${encodeURIComponent(toolName)}`,
      { accessMode },
    ),

  requestIntegration: (payload: HubIntegrationRequestPayload) =>
    hubPost<HubApiIntegrationRequest>("/integrations/requests", {
      source: payload.source,
      slug: payload.slugs?.[0],
      name: payload.name ?? "",
      description: payload.description ?? "",
      docsUrl: payload.docsUrl ?? "",
      notes: payload.notes,
    }),

  approveAccessRequest: (id: string, durationMinutes?: number) =>
    hubPost<HubApiAccessRequest>(
      `/case-by-case-approvals/${id}/approve`,
      durationMinutes != null ? { durationMinutes } : {},
    ),

  rejectAccessRequest: (id: string) =>
    hubPost<HubApiAccessRequest>(`/case-by-case-approvals/${id}/reject`),

  dismissUserRequest: (id: string) =>
    hubPost<HubApiIntegrationRequest>(
      `/admin/integration-requests/${id}/dismiss`,
    ),

  fulfillUserRequest: (id: string) =>
    hubPost<HubApiIntegrationRequest>(`/admin/integration-requests/${id}/add`),

  registerCustomMcp: (input: HubCustomMcpInput) => {
    const authStrategy =
      input.authStrategy === "none" || input.authStrategy === "api_key"
        ? input.authStrategy
        : "oauth2";
    return hubPost<HubApiManagedConnector>("/admin/modify-integrations", {
      slug: input.slug,
      name: input.name,
      description: `Custom MCP server at ${input.serverUrl}`,
      authStrategy,
      provider: "mcp",
      serverUrl: input.serverUrl,
      tools: [],
    });
  },

  createPermissionProfile: (body: {
    name: string;
    sourceProfileId?: string;
    snapshot?: HubPermissionProfileSnapshot;
  }) => hubPost<HubApiPermissionProfile>("/permission-profiles", body),

  patchPermissionProfile: (
    id: string,
    snapshot: HubPermissionProfileSnapshot,
  ) =>
    hubPatch<HubApiPermissionProfile>(`/permission-profiles/${id}`, {
      snapshot,
    }),

  loadPermissionProfile: (id: string) =>
    hubPost<{ profile: HubApiPermissionProfile }>(
      `/permission-profiles/${id}/load`,
    ),

  deletePermissionProfile: (id: string) =>
    hubDelete<{ deleted: boolean }>(`/permission-profiles/${id}`),

  disableUnusedTools: (body: {
    thresholdValue: number;
    thresholdUnit: "days" | "weeks" | "months";
  }) => hubPost<{ updated: number }>("/integrations/disable-unused", body),

  startOAuth: (provider: string) =>
    hubGet<{ redirectUrl?: string; url?: string }>(
      `/oauth/${encodeURIComponent(provider)}/start`,
      { response: "json" },
    ),
};
