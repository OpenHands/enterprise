export type HubAuthStrategy = "oauth2" | "api_key" | "none";

export type HubConnectorProvider = "mcp" | "http";

export interface HubOAuthConfig {
  authorizationUrl?: string;
  tokenUrl?: string;
  scopes?: string[];
  optionalScopes?: string[];
  clientAuthentication?: string;
  registrationUrl?: string;
}

export type HubApprovalStatus = "pending" | "approved" | "denied";

export type HubToolAccessMode = "enabled" | "disabled" | "approval";

export interface HubTool {
  name: string;
  description: string;
  accessMode: HubToolAccessMode;
  defaultScopes: string[];
  lastUsedAt?: string;
  maxAccessMode?: HubToolAccessMode;
}

export interface HubIntegration {
  slug: string;
  name: string;
  description: string;
  connected: boolean;
  enabled: boolean;
  authStrategy: HubAuthStrategy;
  toolCount: number;
  provider: string;
  kind: string;
  tools: HubTool[];
  logoUrl?: string;
  notes?: string;
  categories?: string[];
  docsUrl?: string;
  updatedAt?: string;
  connectorProvider?: HubConnectorProvider;
  serverUrl?: string;
  apiBaseUrl?: string;
  openApiUrl?: string;
  oauthConfig?: HubOAuthConfig;
}

export interface HubApproval {
  id: string;
  toolName: string;
  integrationKey: string;
  integrationName: string;
  status: HubApprovalStatus;
  agentId: string;
  agentClass: string;
  justification?: string;
  scopes: string[];
  requestedMinutes: number;
  createdAt: string;
  decidedBy?: string;
  decidedAt?: string;
  conversationId?: string;
}

export interface HubUserRequest {
  id: string;
  name: string;
  slug: string;
  requestedBy: string;
  notes: string;
  description?: string;
  docsUrl?: string;
  source: "catalog" | "custom";
  createdAt: string;
}

export interface HubIntegrationRequestPayload {
  source: "catalog" | "custom";
  slugs?: string[];
  name?: string;
  description?: string;
  docsUrl?: string;
  notes: string;
}

export interface HubApiKey {
  id: string;
  name: string;
  value: string;
}

export interface HubPermissionProfileSnapshot {
  integrations: Record<
    string,
    {
      enabled: boolean;
      tools: Record<string, HubToolAccessMode>;
    }
  >;
}

export type HubPermissionProfileCreateSource = "duplicate" | "scratch";

export interface HubPermissionProfileCreateInput {
  name: string;
  source: HubPermissionProfileCreateSource;
  sourceProfileId?: string;
}

export interface HubPermissionProfile {
  id: string;
  name: string;
  summary: string;
  updatedAt: string;
  isDefault?: boolean;
  agentApiKey: string;
  snapshot?: HubPermissionProfileSnapshot;
}

export interface HubOverviewConnection {
  integrationKey: string;
  provider: string;
  displayName: string;
  externalAccountId: string;
  authStrategy: HubAuthStrategy;
  updatedAt: string;
}

export interface HubOverviewUser {
  ownerId: string;
  totalConnections: number;
  totalAccessRequests: number;
  pendingAccessRequests: number;
  notifications: number;
  integrations: string[];
  providers: string[];
  connections: HubOverviewConnection[];
  duplicateExternalAccounts: HubDuplicateGroup[];
}

export interface HubDuplicateGroup {
  provider: string;
  externalAccountId: string;
  ownerIds: string[];
  displayNames?: string[];
}

export interface HubCustomMcpInput {
  name: string;
  slug: string;
  serverUrl: string;
  authStrategy: "bearer" | "api_key" | "none";
}

export interface IntegrationsHubViewModel {
  isPersonalWorkspace: boolean;
  showRequestButton: boolean;
  integrations: HubIntegration[];
  catalogIntegrations: HubIntegration[];
  requestableCatalog: HubIntegration[];
  approvals: HubApproval[];
  userRequests: HubUserRequest[];
  apiKeys: HubApiKey[];
  permissionProfiles: HubPermissionProfile[];
  overviewUsers: HubOverviewUser[];
  duplicateGroups: HubDuplicateGroup[];
  connect: (slug: string, seed?: HubIntegration) => void;
  disconnect: (slug: string) => void;
  toggleEnabled: (slug: string) => void;
  updateToolAccess: (
    slug: string,
    toolName: string,
    mode: HubToolAccessMode,
  ) => void;
  requestIntegration: (payload: HubIntegrationRequestPayload) => void;
  decideApprovals: (
    ids: string[],
    status: "approved" | "denied",
    durationHours?: number,
  ) => void;
  dismissUserRequest: (id: string) => void;
  fulfillUserRequest: (id: string) => void;
  registerCustomMcp: (input: HubCustomMcpInput) => void;
  addPermissionProfile: (
    name: string,
    snapshot?: HubPermissionProfileSnapshot,
  ) => void;
  savePermissionProfileSnapshot: (
    id: string,
    snapshot: HubPermissionProfileSnapshot,
  ) => void;
  deletePermissionProfile: (id: string) => void;
  disableUnusedTools: (
    thresholdValue: number,
    thresholdUnit: "days" | "weeks" | "months",
  ) => void;
}
