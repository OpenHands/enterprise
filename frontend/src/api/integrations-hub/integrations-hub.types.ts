/**
 * Integrations Hub API response shapes (from integrations-hub FastAPI).
 * Kept separate from UI ViewModel types; map via adapters.
 */

export type HubApiAuthStrategy = "oauth2" | "api_key" | "none" | "basic";

export type HubApiToolAccessMode = "enabled" | "disabled" | "approval_required";

export interface HubApiToolSpec {
  name: string;
  description?: string;
  accessMode?: HubApiToolAccessMode;
  maxAccessMode?: HubApiToolAccessMode | null;
  defaultScopes?: string[];
  lastInvokedAt?: string | null;
  enabled?: boolean;
}

export interface HubApiIntegrationSpec {
  key: string;
  name: string;
  kind?: string;
  provider?: string;
  authStrategy?: HubApiAuthStrategy;
  enabled?: boolean;
  tools?: Record<string, HubApiToolSpec> | HubApiToolSpec[];
  config?: Record<string, unknown>;
}

export interface HubApiManagedConnector {
  slug: string;
  name: string;
  description?: string;
  logoUrl?: string | null;
  docsUrl?: string | null;
  categories?: string[];
  authStrategy?: HubApiAuthStrategy;
  authModes?: HubApiAuthStrategy[];
  provider?: "mcp" | "http";
  serverUrl?: string | null;
  apiBaseUrl?: string | null;
  openApiUrl?: string | null;
  tools?: Array<{
    name: string;
    description?: string;
    defaultScopes?: string[];
    accessMode?: HubApiToolAccessMode;
  }>;
}

export interface HubApiAccessRequest {
  id: string;
  agentId?: string | null;
  agentClass?: string | null;
  integrationKey: string;
  toolName: string;
  scopes?: string[];
  requestedMinutes?: number;
  justification?: string;
  createdAt: string;
  status: "pending" | "approved" | "denied" | "expired";
  decidedAt?: string | null;
  decidedBy?: string | null;
  agentData?: { conversationId?: string };
}

export interface HubApiIntegrationRequest {
  id: string;
  source: "catalog" | "custom";
  slug?: string | null;
  name: string;
  description?: string;
  docsUrl?: string;
  notes?: string;
  requestedBy: string;
  status?: string;
  createdAt: string;
}

export interface HubApiPermissionProfile {
  id: string;
  name?: string | null;
  snapshot?: {
    integrations?: Record<
      string,
      {
        enabled?: boolean;
        tools?: Record<string, HubApiToolAccessMode>;
      }
    >;
  };
  agentApiKey?: string;
  createdAt?: string;
  updatedAt?: string;
  isDefault?: boolean;
}

export interface HubApiKeyStatus {
  exists?: boolean;
  key?: string | null;
  value?: string | null;
  name?: string | null;
  id?: string | null;
}

export interface HubApiOverviewUser {
  ownerId: string;
  totalConnections: number;
  totalAccessRequests: number;
  pendingAccessRequests: number;
  notifications: number;
  integrations: string[] | Set<string>;
  providers: string[] | Set<string>;
  connections?: Array<Record<string, unknown>>;
  duplicateExternalAccounts?: Array<{
    provider: string;
    externalAccountId: string;
    ownerIds?: string[];
    displayNames?: string[];
  }>;
}

export interface HubApiOverview {
  users?: HubApiOverviewUser[];
  duplicateGroups?: Array<{
    provider: string;
    externalAccountId: string;
    ownerIds: string[];
    displayNames?: string[];
  }>;
}
