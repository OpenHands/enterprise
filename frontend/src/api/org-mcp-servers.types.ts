/**
 * Organization-scoped MCP server listings (OHE-3091).
 *
 * An org admin defines a listing once and every member's conversation
 * launches with it. `managed_by: "org"` listings carry the admin's shared
 * credential; `managed_by: "user"` listings reference a personal secret with
 * `${SECRET_NAME}` and each member connects by creating that secret. Either
 * kind may authenticate with OAuth: the admin authorizes an organization-managed
 * listing once, and each member authorizes a user-managed one with their own
 * account.
 */
export type OrgMcpManagedBy = "org" | "user";

/** One listing as `GET /api/organizations/{org_id}/mcp-servers` returns it. */
export interface OrgMcpServerItem {
  name: string;
  managed_by: OrgMcpManagedBy;
  /** The SDK `MCPServer` config with every credential masked. */
  server: Record<string, unknown>;
  /** Secrets the config references with `${NAME}` and no default. A
   * user-managed listing is connected once each exists as a personal secret. */
  secret_names: string[];
  /** A user-managed OAuth listing: whether the caller has authorized it. */
  connected?: boolean | null;
}

export interface OrgMcpServerPage {
  items: OrgMcpServerItem[];
}

export interface OrgMcpServerCreate {
  name: string;
  managed_by: OrgMcpManagedBy;
  server: Record<string, unknown>;
  /** The finished OAuth job holding an organization-managed listing's tokens. */
  oauth_job_id?: string;
}

export interface OrgMcpServerUpdate {
  managed_by?: OrgMcpManagedBy;
  server?: Record<string, unknown>;
  oauth_job_id?: string;
}
